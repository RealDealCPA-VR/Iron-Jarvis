"""v1.351.0, Wave 1 lane 1C — the chat lanes tell the truth about outcomes.

Four gaps from the product-readiness audit, each pinned through the REAL code
(the real app factory, the real lanes, the real helpers, main.js lifted and run
under node) — never a stub of the thing under test:

* G05 — a chat turn parked on the user's OK with NO window following it is
  listed by ``GET /chat/approvals/pending`` as ``{kind: "chat", thread_id,
  tool}`` (names only, never arguments) and the desktop watcher words it "A
  chat is waiting for your OK" and opens THAT chat. A chat the user is
  looking at is not listed (its card is in front of them).
* G06 — the honesty note no longer accuses real file changes (a resized photo,
  a split PDF, an edit) and now catches a reply that claims a CHANGE no tool
  made ("I updated budget.xlsx" after the edit failed).
* G10 — the offline demo's reply carries ONE note that says it is the demo and
  to choose a model above — no "arm a document tool" advice.
* G14 — the system prompt says where the answering model runs (this PC / the
  user's network / the cloud), from the same predicate as the local-only lock,
  and the route object carries ``on_this_network``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _FILE_CHANGING_TOOLS,
    _FILE_WRITING_TOOLS,
    STRICT_ASK_TOOLS,
    _claimed_write_note,
    _creation_honesty_note,
    demo_note,
    is_demo_route,
    model_where_line,
)
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall
from tests.test_chat_approvals_v1187 import _ASK_MSG, _asgi_post, _shell_call_stream
from tests.test_chat_turn_stop_v1241 import _scope
from tests.test_desktop_reliability_v1249 import _lift, _run, requires_node
from tests.test_durable_chat_turns_v1336 import _get, _until

# --------------------------------------------------------------------------- #
# Shared: open a durable /chat/stream turn whose CONNECTION we drop on demand.
# --------------------------------------------------------------------------- #


class _Window:
    """One dashboard window following a named turn over the real endpoint."""

    def __init__(self, app, body: dict) -> None:
        self.app = app
        self.raw = json.dumps(body).encode()
        self.drop = asyncio.Event()
        self.got: list[str] = []
        self.task: asyncio.Task | None = None

    async def open(self) -> "_Window":
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": self.raw, "more_body": False}
            await self.drop.wait()
            return {"type": "http.disconnect"}

        async def send(msg):
            if msg["type"] == "http.response.body":
                self.got.append(msg.get("body", b"").decode("utf-8", "replace"))

        self.task = asyncio.create_task(self.app(_scope("/chat/stream", self.raw), receive, send))
        return self

    def text(self) -> str:
        return "".join(self.got)

    def approval(self) -> dict:
        for block in self.text().split("\n\n"):
            lines = block.strip().splitlines()
            if lines and lines[0] == "event: approval":
                return json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
        return {}

    async def close(self) -> None:
        self.drop.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=20)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass


async def _pending(app) -> list[dict]:
    status, text = await _get(app, "/chat/approvals/pending")
    assert status == 200, text
    return json.loads(text)["approvals"]


def _chat_rows(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("kind") == "chat"]


_SECRET_ARG = "echo approved-run SECRET-CLIENT-SMITH-1040"


# --------------------------------------------------------------------------- #
# G05 — a chat waiting for your OK is announced.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_a_chat_left_on_its_card_is_listed_once_nobody_watches_it(tmp_path):
    """THE ACCEPTANCE (daemon half): the user asked for something that needs
    an OK, the card appeared, and they left the chat before answering. The
    listing the bell and the desktop watcher read now names that chat — with
    NO arguments — and drops it again once it is answered."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _shell_call_stream(_SECRET_ARG)
    win = await _Window(app, {
        "messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
        "turn_id": "turn-g05", "thread_id": "th-g05",
    }).open()
    try:
        card = await _until(win.approval, seconds=20)
        assert card and card["tool"] == "shell", win.text()[-1500:]
        lg = app.state.platform.turn_logs.get("turn-g05")
        assert lg is not None and lg.watched()

        # While the chat is open in front of the user its card IS the ask:
        # listing it too would double-surface it (the v1.200.0 rule).
        assert _chat_rows(await _pending(app)) == []

        # The user leaves (another page, or the window closed to the tray).
        win.drop.set()
        assert await _until(lambda: not lg.watched()), "the window still follows the turn"
        assert lg.status == "running", "leaving must not stop the turn"

        rows = _chat_rows(await _pending(app))
        assert len(rows) == 1, rows
        row = rows[0]
        assert row["id"] == "chat:th-g05"
        assert row["kind"] == "chat" and row["thread_id"] == "th-g05"
        assert row["tool"] == "shell"
        assert row["requested_at"].endswith("Z")
        # The listing's contract: mission/project present on every row.
        assert row["mission_id"] is None and row["project_id"] is None
        # NEVER ARGUMENTS — the listing fans out to every page and a toast.
        assert "args" not in row
        assert "SECRET-CLIENT" not in json.dumps(await _pending(app))

        # Answered (from the chat, once reopened) → it leaves the listing.
        status, _ = await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"})
        assert status == 200

        async def _gone():
            return _chat_rows(await _pending(app)) == []

        for _ in range(200):
            if await _gone():
                break
            await asyncio.sleep(0.05)
        assert await _gone(), "an answered chat ask is still announced"
    finally:
        await win.close()
        lg = app.state.platform.turn_logs.get("turn-g05")
        if lg is not None and lg.task is not None and not lg.task.done():
            lg.task.cancel()


@pytest.mark.asyncio
async def test_a_new_chats_first_turn_is_listed_under_the_chat_it_was_linked_to(tmp_path):
    """A NEW chat's first question is sent before its first save names the
    chat, so its turn has no per-chat entry in ``TURNS.thread_states()`` —
    a waiting-state lookup alone would never list it. The turn's own open
    card (still in the shared registry) does, under the chat it was linked
    to after the fact."""
    app = create_app(str(tmp_path))
    app.state.platform.router.stream = _shell_call_stream(_SECRET_ARG)
    win = await _Window(app, {
        "messages": [{"role": "user", "content": _ASK_MSG}], "auto_tools": True,
        "turn_id": "turn-new",
    }).open()
    try:
        card = await _until(win.approval, seconds=20)
        assert card, win.text()[-1500:]
        win.drop.set()
        lg = app.state.platform.turn_logs.get("turn-new")
        assert await _until(lambda: not lg.watched())

        rows = _chat_rows(await _pending(app))
        assert [(r["id"], r["thread_id"], r["tool"]) for r in rows] == [
            ("chat:turn:turn-new", "", "shell")
        ], "an unlinked new chat's ask was never announced"

        assert (await _asgi_post(app, "/chat/turns/turn-new/thread", {"thread_id": "th-new"}))[0] == 200
        rows = _chat_rows(await _pending(app))
        assert [(r["id"], r["thread_id"]) for r in rows] == [("chat:th-new", "th-new")]
        await _asgi_post(app, f"/chat/approvals/{card['id']}", {"decision": "deny"})
    finally:
        await win.close()
        lg = app.state.platform.turn_logs.get("turn-new")
        if lg is not None and lg.task is not None and not lg.task.done():
            lg.task.cancel()


def test_the_helper_lists_only_running_unwatched_turns_that_wait():
    """``waiting_unwatched`` — the rule, on the real TurnLog/TurnLogs."""
    logs = dt.TurnLogs(engine=None)

    def _log(turn_id: str, thread: str) -> dt.TurnLog:
        lg = dt.TurnLog(turn_id, thread)
        logs._logs[turn_id] = lg
        return lg

    asked = _log("t1", "th-1")
    asked.add(dt.sse("approval", {"id": "ap_1", "tool": "write_file", "args": {"path": "C:/x/Smith.pdf"}}))
    _log("t2", "th-2")  # parked on an app's question: no card, the chat says waiting
    _log("t3", "th-3")  # running, nobody asked anything
    seen = _log("t4", "th-4")  # waiting, but a window follows it
    seen.add(dt.sse("approval", {"id": "ap_4", "tool": "shell"}))
    seen._followers.add(asyncio.Queue())
    done = _log("t5", "th-5")  # finished: never listed
    done.add(dt.sse("approval", {"id": "ap_5", "tool": "shell"}))
    done.status = "done"
    answered = _log("t6", "th-6")  # its card was answered
    answered.add(dt.sse("approval", {"id": "ap_6", "tool": "shell"}))
    answered.add(dt.sse("approval_resolved", {"id": "ap_6", "decision": "once"}))
    gone = _log("t7", "th-7")  # its card left the registry (stopped, timed out)
    gone.add(dt.sse("approval", {"id": "ap_7", "tool": "shell"}))

    states = {"th-2": {"running": True, "waiting": True}, "th-3": {"running": True, "waiting": False}}
    rows = dt.waiting_unwatched(logs, states, ["ap_1", "ap_4", "ap_5", "ap_6"])
    assert [(r["id"], r["tool"]) for r in rows] == [("chat:th-1", "write_file"), ("chat:th-2", "")]
    assert "Smith" not in json.dumps(rows), "a card's arguments reached the listing"
    assert all(set(r) == {"id", "kind", "thread_id", "tool", "requested_at", "mission_id", "project_id"} for r in rows)
    # Never raises; no logs = nothing.
    assert dt.waiting_unwatched(None, states, ["ap_1"]) == []


# --- the desktop watcher (main.js, lifted and run under node) --------------- #
_PLAN_HARNESS = """
const notes = [];
let tray = { setToolTip: () => {} };
const _trayDegraded = new Set();
__LIFTED__
const s = JSON.parse(process.argv[2]);
let seen = {};
const out = [];
for (const step of s.steps) {
  const plan = planAskNotifications(seen, step.approvals, step.now, !!step.windowOpen);
  seen = plan.seen;
  out.push({ notify: plan.notify, waiting: plan.waiting });
}
process.stdout.write(JSON.stringify({ out }) + "\\n");
"""

_CLICK_HARNESS = """
const loaded = [];
let mainWin = null;
const DASHBOARD_URL = "http://127.0.0.1:8788";
function showMainWindow() {
  mainWin = { isDestroyed: () => false, loadURL: (u) => loaded.push(u) };
}
class Notification {
  constructor(o) { this.o = o; this.h = {}; }
  on(e, f) { this.h[e] = f; }
  show() { this.h.click(); }
}
__LIFTED__
for (const it of JSON.parse(process.argv[2])) showAskNotification(it);
process.stdout.write(JSON.stringify({ loaded }) + "\\n");
"""

_HOUR = 60 * 60 * 1000


def _plan(steps: list, tmp_path: Path) -> list:
    script = _PLAN_HARNESS.replace(
        "__LIFTED__", _lift("const ASK_WATCH_MS", "function planAskNotifications")
    )
    return _run(script, tmp_path, "plan13510", json.dumps({"steps": steps}))["out"]


@requires_node
def test_the_tray_says_a_chat_is_waiting_for_your_ok(tmp_path):
    """THE ACCEPTANCE (desktop half): the window is closed to the tray and the
    listing carries a chat row — the toast says so in its own words, once,
    and never quotes an argument."""
    chat = {"id": "chat:th-g05", "kind": "chat", "thread_id": "th-g05", "tool": "write_file"}
    out = _plan(
        [
            {"approvals": [chat], "now": 0},
            {"approvals": [chat], "now": 25_000},
        ],
        tmp_path,
    )
    first = out[0]["notify"]
    assert len(first) == 1
    assert first[0]["title"] == "A chat is waiting for your OK"
    assert first[0]["body"] == "save a file — click to open the chat."
    assert first[0]["chat"] is True and first[0]["threadId"] == "th-g05"
    assert first[0]["sessionId"] == "", "a chat row must not open a job page"
    assert out[1]["notify"] == [], "it re-announced the same chat on the next tick"
    assert out[0]["waiting"] == 1


@requires_node
def test_a_chat_on_an_apps_question_is_not_called_use_a_tool(tmp_path):
    chat = {"id": "chat:th-2", "kind": "chat", "thread_id": "th-2", "tool": ""}
    out = _plan([{"approvals": [chat], "now": 0}, {"approvals": [chat], "now": _HOUR + 1}], tmp_path)
    assert out[0]["notify"][0]["body"] == "Click to open the chat."
    later = out[1]["notify"][0]
    assert later["title"] == "A chat is still waiting for your OK"
    assert later["body"] == "It has been waiting 1 hour — click to open the chat."


@requires_node
def test_an_open_window_leaves_the_chat_row_to_the_bell(tmp_path):
    """The existing rule holds for chat rows too: while a window is open the
    page's bell owns the announcing (no double toast)."""
    chat = {"id": "chat:th-9", "kind": "chat", "thread_id": "th-9", "tool": "shell"}
    out = _plan([{"approvals": [chat], "now": 0, "windowOpen": True}], tmp_path)
    assert out[0]["notify"] == [] and out[0]["waiting"] == 1


@requires_node
def test_the_toast_click_opens_that_chat(tmp_path):
    script = _CLICK_HARNESS.replace(
        "__LIFTED__", _lift("function showAskNotification", "function showAskNotification")
    )
    items = [
        {"title": "t", "body": "b", "chat": True, "threadId": "th g/05", "sessionId": ""},
        {"title": "t", "body": "b", "chat": True, "threadId": "", "sessionId": ""},
        {"title": "t", "body": "b", "sessionId": "sess_7"},
    ]
    loaded = _run(script, tmp_path, "click13510", json.dumps(items))["loaded"]
    assert loaded == [
        "http://127.0.0.1:8788/chat?thread=th%20g%2F05",
        "http://127.0.0.1:8788/chat",
        "http://127.0.0.1:8788/sessions/sess_7",
    ]


# --------------------------------------------------------------------------- #
# G06 — the honesty note stops accusing real file changes.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool", ["image_resize", "image_convert", "pdf_split", "pdf_arrange", "edit_file", "rename_file"]
)
def test_a_real_file_change_is_never_accused(tool):
    reply = "I made it smaller and saved it as photo_small.png."
    assert _claimed_write_note(reply, [tool]) == ""
    assert _claimed_write_note("I updated budget.xlsx with the new totals.", [tool]) == ""


def test_the_wider_set_does_not_widen_the_strict_ask_gate():
    """`_FILE_WRITING_TOOLS` feeds STRICT_ASK_TOOLS and the write directive; the
    honesty notes read the wider set, kept separate on purpose."""
    assert _FILE_WRITING_TOOLS < _FILE_CHANGING_TOOLS
    assert "image_resize" not in _FILE_WRITING_TOOLS
    assert "image_resize" not in STRICT_ASK_TOOLS
    assert "read_document" not in _FILE_CHANGING_TOOLS  # anti-vacuity


@pytest.mark.parametrize(
    "reply",
    [
        "I updated budget.xlsx with the new totals.",
        "I've updated the Q3 totals in budget.xlsx.",
        "Done. Updated budget.xlsx with the new rates.",
        "Done — updated budget.xlsx with the new rates.",
        "Changes made:\n- Updated budget.xlsx (rates)\n- Checked the totals",
        "I opened the file and changed the fee in budget.xlsx.",
        "budget.xlsx has been updated with the new totals.",
        "I renamed the scan to Smith_2025.pdf.",
    ],
)
def test_a_claimed_change_no_tool_made_gets_the_note(reply):
    note = _claimed_write_note(reply, ["read_document"])
    assert note, reply
    assert "did not change" in note
    assert "does not exist" not in note, "a file that was CHANGED existed before"


@pytest.mark.parametrize(
    "reply",
    [
        "Here is what changed in report.docx since last year.",
        "The totals changed in budget.xlsx between Q2 and Q3.",
        "You updated budget.xlsx yesterday, so the figures are current.",
        "I have not updated budget.xlsx.",
        "Once budget.xlsx has been updated, the chart follows.",
        "The file was last modified in March; budget.xlsx shows the old rate.",
    ],
)
def test_describing_a_change_is_not_claiming_one(reply):
    assert _claimed_write_note(reply, ["read_document"]) == "", reply


def test_a_creation_claim_reads_exactly_as_before():
    note = _claimed_write_note("Saved to report.docx.", ["read_document"])
    assert note == (
        "\n\n_Note: nothing was written to disk this turn. The reply mentions "
        "`report.docx`, but no document-writing tool ran — so that file does not "
        "exist. Ask again and arm a document tool (the “+” menu, or keep "
        "Auto-tools on)._"
    )


def test_the_creation_note_counts_a_real_resize_as_the_file():
    body = SimpleNamespace(messages=[SimpleNamespace(role="user", content="create a smaller copy of this photo as a png file")])
    # Anti-vacuity: the user DID ask for a file, so with nothing run the note fires.
    assert _creation_honesty_note(body, ["image_resize"], []) != ""
    assert _creation_honesty_note(body, ["image_resize"], ["image_resize"]) == ""


class _ScriptedModel(LLMAdapter):
    """A REAL-model stand-in (not the demo): round 1 calls one tool, round 2
    answers with ``final``."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, call: ToolCall, final: str) -> None:
        self.call, self.final, self.n = call, final, 0

    async def complete(self, *, system, messages, tools, **_kw):
        self.n += 1
        if self.n == 1:
            return LLMResponse(text="", tool_calls=[self.call])
        return LLMResponse(text=self.final)


def _png(path: Path, w: int = 200, h: int = 120) -> None:
    from PIL import Image

    Image.new("RGB", (w, h), (200, 30, 30)).save(path, format="PNG")


def test_a_photo_made_smaller_shows_the_file_and_no_accusation(tmp_path):
    """THE ACCEPTANCE: the user asks to make a photo smaller; the resized file
    is on the receipt and there is no 'nothing was written' note."""
    client = TestClient(create_app(str(tmp_path / "home")))
    ws = tmp_path / "work"
    ws.mkdir()
    _png(ws / "photo.png")
    model = _ScriptedModel(
        ToolCall(id="c1", name="image_resize",
                 arguments={"source": "photo.png", "target": "photo_small.png", "max_width": 50}),
        "I made the photo smaller and saved it as photo_small.png.",
    )
    client.app.state.platform.providers.get = lambda p, m=None: model
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "make this photo smaller"}],
        "tools": ["image_resize"], "workspace_dir": str(ws),
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert "image_resize" in out["tools_used"], out
    assert (ws / "photo_small.png").is_file()
    assert any(str(p).endswith("photo_small.png") for p in out["documents"]), out["documents"]
    assert "nothing was written" not in out["reply"], out["reply"]
    assert "does not exist" not in out["reply"]


def test_a_reply_claiming_an_edit_that_failed_gets_the_note(tmp_path):
    """THE ACCEPTANCE: the edit failed (no such workbook), the reply says it
    updated budget.xlsx anyway — the note says it did not change."""
    client = TestClient(create_app(str(tmp_path / "home")))
    ws = tmp_path / "work"
    ws.mkdir()
    model = _ScriptedModel(
        ToolCall(id="c1", name="excel_edit",
                 arguments={"path": "budget.xlsx", "edits": [{"cell": "B2", "value": 9}]}),
        "I updated budget.xlsx with the new totals.",
    )
    client.app.state.platform.providers.get = lambda p, m=None: model
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "put 9 in B2 of budget.xlsx"}],
        "tools": ["excel_edit"], "workspace_dir": str(ws),
    })
    assert r.status_code == 200, r.text
    out = r.json()
    assert "excel_edit" not in out["tools_used"], "the failed edit counted as run"
    assert "The reply says it changed `budget.xlsx`" in out["reply"], out["reply"]
    assert "did not change" in out["reply"]


# --------------------------------------------------------------------------- #
# G10 — a demo reply says it is the demo.
# --------------------------------------------------------------------------- #
def _stream_done(client: TestClient, body: dict) -> dict:
    r = client.post("/chat/stream", json=body)
    assert r.status_code == 200, r.text
    done = None
    for block in r.text.split("\n\n"):
        lines = block.strip().splitlines()
        if lines and lines[0] == "event: done":
            done = json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: ")))
    assert done is not None, r.text[-1500:]
    return done


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_what_can_you_do_on_the_demo_says_it_is_the_demo_once(tmp_path, lane):
    """THE ACCEPTANCE: a brand-new user presses 'What can you do?' before
    choosing a model — ONE demo note telling them to choose a model above,
    and no 'arm a document tool' advice."""
    with TestClient(create_app(str(tmp_path))) as client:  # fresh install: the demo
        body = {"messages": [{"role": "user", "content": "What can you do?"}]}
        out = client.post("/chat", json=body).json() if lane == "post" else _stream_done(client, body)
    assert out["route"]["provider"] == "mock" and out["route"]["reason"] == "mock"
    reply = out["reply"]
    assert reply.count("offline demo") == 1, reply
    assert "Choose a model above to get real answers." in reply
    assert "arm a document tool" not in reply and "Arm write_document" not in reply, reply
    assert "nothing was written to disk this turn" not in reply
    assert reply.count("_Note:") == 1, reply


def test_a_file_request_on_the_demo_gets_the_demo_note_not_the_tool_advice(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        out = client.post("/chat", json={
            "messages": [{"role": "user", "content": "Turn my notes into a clean Word document"}],
        }).json()
    assert "offline demo" in out["reply"]
    assert "Arm write_document" not in out["reply"] and "arm a document tool" not in out["reply"]


def test_the_demo_note_never_says_nothing_was_written_when_it_wrote():
    """With a file tool armed the demo REALLY writes its RESULT.md, so the
    note then says only what the reply is."""
    assert "nothing was written" in demo_note([])
    assert "nothing was written" not in demo_note(["write_file"])
    assert "Choose a model above" in demo_note(["write_file"])


def test_only_the_routed_demo_is_the_demo():
    assert is_demo_route("mock", "mock")
    # A double that only LABELS itself mock (no router) is not the demo, and
    # a real provider never is.
    assert not is_demo_route("mock", "default")
    assert not is_demo_route("anthropic", "default")
    assert not is_demo_route("", "")


# --------------------------------------------------------------------------- #
# G14 — a cloud model is not told it runs locally.
# --------------------------------------------------------------------------- #
class _Box(LLMAdapter):
    """An answering model with the node the real fleet adapters carry (the
    address lives there); records the system prompt it was sent."""

    def __init__(self, provider: str, base_url: str = "") -> None:
        self.provider = provider
        self.model = "m1"
        self.node = SimpleNamespace(label="", base_url=base_url)
        self.systems: list[str] = []

    def capabilities(self):
        return {"provider": self.provider, "model": "m1", "tool_use": True, "vision": False}

    async def complete(self, *, system, messages, tools, **_kw):
        self.systems.append(system)
        return LLMResponse(text="hello")


def _wire(client: TestClient, monkeypatch, box: _Box) -> _Box:
    platform = client.app.state.platform
    real_get, real_avail = platform.providers.get, platform.providers.available
    monkeypatch.setattr(platform.providers, "get", lambda p, m=None: box if p == box.provider else real_get(p, m))
    monkeypatch.setattr(platform.providers, "available", lambda n: True if n == box.provider else real_avail(n))
    return box


_HI = {"messages": [{"role": "user", "content": "are you running locally?"}]}


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_cloud_model_is_told_it_runs_in_the_cloud(tmp_path, monkeypatch, lane):
    """THE ACCEPTANCE: a chat answered by a cloud model is told so in its
    system prompt, and the route object carries on_this_network."""
    with TestClient(create_app(str(tmp_path))) as client:
        box = _wire(client, monkeypatch, _Box("anthropic"))
        body = {**_HI, "provider": "anthropic", "model": "m1"}
        out = client.post("/chat", json=body).json() if lane == "post" else _stream_done(client, body)
    system = box.systems[0]
    assert "You run locally" not in system
    assert "The model answering (you) runs in the cloud" in system
    assert "never say you run locally" in system
    assert out["route"]["on_this_network"] is False
    assert list(out["route"])[-1] == "on_this_network", "additive keys go LAST"


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_local_fleet_model_is_told_it_runs_on_the_users_network(tmp_path, monkeypatch, lane):
    with TestClient(create_app(str(tmp_path))) as client:
        box = _wire(client, monkeypatch, _Box("fleet-spark1", "http://192.168.1.50:8000"))
        body = {**_HI, "provider": "fleet-spark1", "model": "m1"}
        out = client.post("/chat", json=body).json() if lane == "post" else _stream_done(client, body)
    system = box.systems[0]
    assert "The model answering (you) runs on a computer on the user's own network" in system
    assert "runs in the cloud" not in system and "You run locally" not in system
    assert out["route"]["on_this_network"] is True


def test_the_where_line_follows_the_one_predicate():
    """Every place word comes from egress.model_place, which reads
    stays_on_this_network — the local-only lock's own predicate."""
    loop = SimpleNamespace(get=lambda p, m=None: SimpleNamespace(node=SimpleNamespace(base_url="http://127.0.0.1:11434")))
    lan = SimpleNamespace(get=lambda p, m=None: SimpleNamespace(node=SimpleNamespace(base_url="http://spark1.ts.net:8000")))
    public = SimpleNamespace(get=lambda p, m=None: SimpleNamespace(node=SimpleNamespace(base_url="https://ollama.com")))
    assert egress.model_place("fleet-a", loop) == egress.PLACE_THIS_PC
    assert egress.model_place("fleet-a", lan) == egress.PLACE_NETWORK
    assert egress.model_place("fleet-a", public) == egress.PLACE_CLOUD, "a local KIND at a public address is the cloud"
    assert egress.model_place("claude-cli") == egress.PLACE_CLOUD
    assert egress.model_place("mock") == egress.PLACE_THIS_PC
    for name, manager in (("fleet-a", loop), ("fleet-a", lan), ("fleet-a", public), ("claude-cli", None)):
        assert (egress.model_place(name, manager) != egress.PLACE_CLOUD) == egress.stays_on_this_network(name, manager)

    def line(provider, manager=None):
        return model_where_line(SimpleNamespace(providers=manager, config=None), provider)

    assert "runs on this PC" in line("fleet-a", loop)
    assert "runs on a computer on the user's own network" in line("fleet-a", lan)
    assert "runs in the cloud" in line("fleet-a", public)
    assert "picked per message (Auto)" in line("auto")
    assert "do not claim to run locally" in line("")
    assert all(line(p).startswith("- Iron Jarvis runs on the user's machine; their home directory is ")
               for p in ("auto", "claude-cli", ""))


def test_both_lanes_build_the_environment_line_from_the_shared_helper():
    """Lock-step: the old hard-coded sentence is gone from BOTH lanes, and
    both call the one helper (a line that lands in one lane only is half a
    fix — the stream lane is the one users watch)."""
    root = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"
    for rel, call in (("chat_turn.py", "model_where_line(platform, _where_provider)"),
                      ("routes/chat.py", "_chat_turn.model_where_line(d.platform, _where_provider)")):
        src = (root / rel).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert "You run locally on the user's machine" not in src, rel
        assert call in src, rel


def test_the_route_flags_carry_on_this_network_last():
    assert list(egress.route_flags("claude-cli")) == ["local_only", "allowed_once", "on_this_network"]
    assert egress.route_flags("claude-cli")["on_this_network"] is False
    assert egress.route_flags("mock")["on_this_network"] is True


def test_chat_turn_module_exports_the_shared_helpers_the_stream_lane_calls():
    for name in ("model_where_line", "is_demo_route", "demo_note"):
        assert callable(getattr(chat_turn, name)), name
