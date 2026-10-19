"""v1.353.0 lane 3B — the chat lanes, the Guide, media and fix doors.

Three product-readiness gaps, each driven as a new user meets it, through the
REAL app (``create_app``), the REAL autoselect, the REAL registry and tools.
The MODEL is the only double (it records what it was shown and answers round
by round); nothing under test is stubbed.

* G38 — "What can you do?" (the first suggestion pill) and the how-do-I
  questions a first week asks ("how do I chat with you from my phone") arm
  the Guide's lookups, and the Guide's search answers from the Handbook.
* G40 — asking for a picture with no media service connected shows the
  model ``app_connect`` (its pixio branch is the secure key card) instead of
  ``generate_media`` (an approval card that then failed); both lanes say so in
  the Tools section; the refusal words name the Settings place, never a vault
  name or an environment variable; saving the key on the card really connects
  the service the next turn reads.
* G41 — a call that failed because something is not SET UP leaves a button
  to the fix in the reply's ``doors`` (both lanes): "Connect Google Drive in
  Settings" for an app that signs in through the browser, "Pair your browser"
  for an unpaired browser; a declined card leaves none; an app tool's refusal
  footnote reads "GitHub › create issue …", never ``mcp__github__…``.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.connectors.catalog import get_connector
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    NO_MEDIA_CONNECT_LINE,
    NO_MEDIA_SETTINGS_LINE,
    app_tool_words,
    media_service_ready,
    no_media_service_line,
    refusal_footnotes,
    run_chat_turn,
    swap_media_for_connect,
)
from iron_jarvis.daemon.doors import collect_doors, failure_door_for
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.media.fake import FakeProvider
from iron_jarvis.media.registry import CONNECT_HINTS, MediaRegistry
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import select_auto_tools
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult

#: THE CONTRACT FIXTURE: the ``doors`` the REAL daemon put on the done frame
#: for the two acceptance turns, read by
#: ``dashboard/__tests__/fix-doors-v13530.test.tsx`` through the real
#: DoorsStrip. Regenerate with
#: ``IJ_REGEN_FIX_DOORS=1 uv run pytest tests/test_chat_guide_media_doors_v13530.py``.
FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "fix-doors-v13530.json"
REGEN = os.environ.get("IJ_REGEN_FIX_DOORS") == "1"


def _check_fixture(section: str, actual: Any) -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    if REGEN:
        data["_comment"] = (
            "v1.353.0 lane 3B: the doors the REAL daemon sends for a refusal whose fix is "
            "a press. Written by tests/test_chat_guide_media_doors_v13530.py "
            "(IJ_REGEN_FIX_DOORS=1); read by dashboard/__tests__/fix-doors-v13530.test.tsx."
        )
        data[section] = actual
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert section in data, f"fixture has no {section!r}: regenerate it"
    assert data[section] == actual, f"the daemon's {section!r} doors no longer match the dashboard's fixture"


GUIDE = {"guide_search", "app_search", "app_status"}
SETTINGS_PLACE = "Settings › Connections"  # v1.354.0: the row is named like its page (G67)
DRIVE_DOOR = {"href": "/settings?section=connections-apps", "label": "Connect Google Drive in Settings"}
PAIR_DOOR = {"href": "/computeruse", "label": "Pair your browser"}
MAKE = "make a picture of a cat surfing"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # A developer's own keys must never make the "nothing connected" install
    # look connected.
    for var in ("PIXIO_API_KEY", "OPENAI_API_KEY", "IRONJARVIS_MEDIA_FAKE"):
        monkeypatch.delenv(var, raising=False)
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


# --------------------------------------------------------------------------- #
# harness — the model is the only double
# --------------------------------------------------------------------------- #


def _final(text: str = "", calls: list[ToolCall] | None = None) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=list(calls or []), usage={})


class _StreamModel:
    """The stream lane's model: records what it was SHOWN (tool names, the
    system prompt, the messages) and plays ``rounds`` — a list of tool-call
    lists; an empty list (or running out) answers "Done."."""

    def __init__(self, rounds: list[list[ToolCall]]) -> None:
        self.rounds = list(rounds)
        self.n = 0
        self.tools: list[list[str]] = []
        self.systems: list[str] = []
        self.messages: list[list[Any]] = []

    async def __call__(self, *args: Any, **kw: Any):
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        self.systems.append(str(kw.get("system") or ""))
        self.messages.append(list(kw.get("messages") or []))
        i = self.n
        self.n += 1
        calls = self.rounds[i] if i < len(self.rounds) else []
        if calls:
            yield {"type": "final", "response": _final("", calls), "provider": "mock", "model": "mock"}
            return
        yield {"type": "text", "text": "Done."}
        yield {"type": "final", "response": _final("Done."), "provider": "mock", "model": "mock"}


class _PostModel:
    """The POST lane's router double, same contract as :class:`_StreamModel`."""

    default_provider = "mock"

    def __init__(self, rounds: list[list[ToolCall]]) -> None:
        self.rounds = list(rounds)
        self.n = 0
        self.tools: list[list[str]] = []
        self.systems: list[str] = []

    async def complete(self, **kw: Any) -> RouteResult:
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        self.systems.append(str(kw.get("system") or ""))
        i = self.n
        self.n += 1
        calls = self.rounds[i] if i < len(self.rounds) else []
        if calls:
            return RouteResult(_final("", calls), "mock", "mock")
        return RouteResult(_final("Done."), "mock", "mock")


def _frames(chunk: Any) -> list[tuple[str, dict]]:
    text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
    out = []
    for block in text.split("\n\n"):
        ev, data = "", None
        for line in block.splitlines():
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if ev:
            out.append((ev, data or {}))
    return out


def _stream(app, body: dict, *, answer: str = "once", cards: list | None = None) -> list[tuple[str, dict]]:
    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, ChatBody(**body))
        seen = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    if cards is not None:
                        cards.append(data)
                    approvals.resolve(data["id"], answer)
        return seen

    return asyncio.run(asyncio.wait_for(go(), 30))


def _done(frames) -> dict:
    return [d for ev, d in frames if ev == "done"][-1]


def _body(text: str, *, tools: list[str] | None = None) -> dict:
    """A stream body as the chat page sends it: the Auto switch on, so the
    SENTENCE arms (and ask-arms) exactly as for a user."""
    return {
        "messages": [{"role": "user", "content": text}],
        "tools": tools or [], "approval_mode": "approve_for_me", "auto_tools": True,
    }


def _tools_section(system: str) -> str:
    return system[system.index("\n# Tools\n"):] if "\n# Tools\n" in system else ""


def _ctx(platform, workspace: Path) -> ToolContext:
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace, session_id="chat", agent_run_id="chat",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


# =========================================================================== #
# G38 — "What can you do?" and how-do-I questions ask the Guide
# =========================================================================== #

ABOUT_JARVIS = [
    "What can you do?",
    "what could you help me with?",
    "What else can you do?",
    "What are you able to do?",
    "what are your capabilities?",
    "how do I chat with you from my phone",
    "how do I connect gmail",
    "how do I schedule something",
    "How do I text you?",
    "how do I add a tool",
    "how do I install the browser add-on",
    "how do I pair my browser",
    "how do I send you a file",
    "how do I automate my month-end close",
    "can I talk to you from my phone?",
    "can you run this report on a schedule?",
    "can you read pages from my browser?",
    # the v1.224.0 phrasings still arm
    "how do I turn on computer use",
    "where in the app is the undo page",
]


@pytest.mark.parametrize("sentence", ABOUT_JARVIS)
def test_a_question_about_jarvis_arms_the_guides_three_lookups(sentence):
    picked = set(select_auto_tools(sentence, cap=99))
    assert GUIDE <= picked, f"{sentence!r} armed {sorted(picked)}"


NOT_ABOUT_JARVIS = [
    "what can you do about the late fee?",
    "what can you do to fix this formula",
    "hey, how are you today?",
    "what is the capital of France",
    "what does this function do",
    "summarise this pdf",
    "write me an email to the client about the invoice",
    "what's the difference between a 1065 and an 1120S",
]


@pytest.mark.parametrize("sentence", NOT_ABOUT_JARVIS)
def test_ordinary_questions_arm_no_guide_lookup(sentence):
    assert not (GUIDE & set(select_auto_tools(sentence, cap=99))), sentence


def test_what_can_you_do_reaches_the_model_with_the_guide_in_the_stream_lane(app):
    model = _StreamModel([[]])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("What can you do?"))
    assert GUIDE <= set(model.tools[0]), model.tools[0]
    assert _done(frames)["reply"].startswith("Done.")


def test_what_can_you_do_reaches_the_model_with_the_guide_in_the_post_lane(app):
    platform = app.state.platform
    platform.router = _PostModel([[]])
    body = ChatBody(messages=[{"role": "user", "content": "What can you do?"}], auto_tools=True)
    asyncio.run(run_chat_turn(platform, {}, body))
    assert GUIDE <= set(platform.router.tools[0]), platform.router.tools[0]


def test_the_phone_question_reaches_the_model_with_the_guide(app):
    model = _StreamModel([[]])
    app.state.platform.router.stream = model
    _stream(app, _body("how do I chat with you from my phone"))
    assert GUIDE <= set(model.tools[0]), model.tools[0]


def test_the_guides_answer_to_what_can_you_do_is_drawn_from_the_handbook(app):
    """The acceptance's second half, through the real lane: the model asks
    the armed Guide, the REAL guide_search runs (allow tier — no card) and
    what reaches the model's next round is the Handbook's own text."""
    model = _StreamModel([
        [ToolCall(id="g1", name="guide_search", arguments={"query": "What can you do?"})],
        [],
    ])
    app.state.platform.router.stream = model
    cards: list[dict] = []
    frames = _stream(app, _body("What can you do?"), cards=cards)
    assert cards == [], "a read-only Guide lookup must never card"
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert [f["name"] for f in finished] == ["guide_search"] and finished[0]["ok"] is True
    assert "Handbook" in finished[0]["output"], finished[0]["output"][:400]
    tool_msgs = [m for m in model.messages[1] if getattr(m, "role", "") == "tool"]
    assert tool_msgs and "Handbook" in str(tool_msgs[-1].content)
    assert "guide_search" in _done(frames)["tools_used"]


# =========================================================================== #
# G40 — asking for a picture with no media service asks for the key up front
# =========================================================================== #


def test_a_fresh_install_has_no_media_service(app):
    """Anti-vacuity: the REAL registry (Pixio + OpenAI Images) on an empty
    home answers "nothing connected"."""
    platform = app.state.platform
    assert [p.id for p in platform.media.providers()] == ["pixio", "openai_images"]
    assert asyncio.run(media_service_ready(platform)) is False


def test_no_media_service_shows_the_pixio_key_card_instead_of_an_approval_that_fails(app):
    platform = app.state.platform
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "pixio"})], []])
    platform.router.stream = model
    cards: list[dict] = []
    frames = _stream(app, _body(MAKE), cards=cards)

    shown = set(model.tools[0])
    assert "generate_media" not in shown, "the approval that then fails is gone"
    assert "app_connect" in shown, "the key card's tool is what the model sees"
    tools = _tools_section(model.systems[0])
    assert NO_MEDIA_CONNECT_LINE in tools and NO_MEDIA_SETTINGS_LINE not in tools
    assert "generate_media" not in [c.get("tool") for c in cards]

    done = _done(frames)
    secret = [c for c in done["config_cards"] if c.get("kind") == "secret"]
    assert len(secret) == 1, done["config_cards"]
    assert secret[0]["name"] == "connection.pixio"
    assert secret[0]["label"] == "Pixio API key"
    assert secret[0]["request_id"]
    assert "app_connect" in done["tools_used"]
    assert "generate_media" not in done["tools_used"]


def test_the_key_saved_on_that_card_connects_the_service_the_next_turn_reads(app):
    """The card's save (POST /config/secret, name ``connection.pixio``)
    lands where the media registry looks, so the very next "make a picture"
    keeps ``generate_media`` and says nothing about a missing service."""
    platform = app.state.platform
    with TestClient(app) as client:
        r = client.post("/config/secret", json={"name": "connection.pixio", "value": "pxio_live_cardtest"})
        assert r.status_code == 200, r.text
        assert "pxio_live_cardtest" not in r.text
    assert asyncio.run(media_service_ready(platform)) is True
    model = _StreamModel([[]])
    platform.router.stream = model
    _stream(app, _body(MAKE))
    assert "generate_media" in model.tools[0] and "app_connect" not in model.tools[0]
    assert "NO MEDIA SERVICE" not in model.systems[0]


def test_with_a_media_service_connected_the_sentence_keeps_generate_media(app):
    """The control: the swap happens ONLY when nothing is connected."""
    platform = app.state.platform
    platform.media = MediaRegistry([FakeProvider(enabled=True)], config=platform.config)
    model = _StreamModel([[]])
    platform.router.stream = model
    _stream(app, _body(MAKE))
    assert "generate_media" in model.tools[0] and "app_connect" not in model.tools[0]
    assert "NO MEDIA SERVICE" not in model.systems[0]


def test_when_the_vault_cannot_say_the_turn_is_armed_as_before(app):
    class _Unreadable:
        def available(self):
            raise RuntimeError("the vault is locked")

    platform = app.state.platform
    platform.media = _Unreadable()
    assert asyncio.run(media_service_ready(platform)) is None
    model = _StreamModel([[]])
    platform.router.stream = model
    _stream(app, _body(MAKE))
    assert "generate_media" in model.tools[0]
    assert "NO MEDIA SERVICE" not in model.systems[0]


def test_an_explicit_pick_of_generate_media_stays_and_is_told_where_the_key_goes(app):
    """A "+" pick is consent: it is never swapped. With no service the
    model is told where the key goes (app_connect is not in reach)."""
    platform = app.state.platform
    model = _StreamModel([[]])
    platform.router.stream = model
    _stream(app, _body("hello there", tools=["generate_media"]))
    assert "generate_media" in model.tools[0] and "app_connect" not in model.tools[0]
    tools = _tools_section(model.systems[0])
    assert NO_MEDIA_SETTINGS_LINE in tools and NO_MEDIA_CONNECT_LINE not in tools


def test_an_explicit_pick_is_told_where_the_key_goes_with_auto_tools_off_too(app):
    """Lock-step with the POST lane, which has no Auto switch to read: the
    line does not depend on the sentence pass running."""
    platform = app.state.platform
    model = _StreamModel([[]])
    platform.router.stream = model
    body = {**_body("hello there", tools=["generate_media"]), "auto_tools": False}
    _stream(app, body)
    assert "generate_media" in model.tools[0]
    assert NO_MEDIA_SETTINGS_LINE in _tools_section(model.systems[0])


def test_the_post_lane_says_where_the_key_goes_for_an_explicit_pick(app):
    platform = app.state.platform
    platform.router = _PostModel([[]])
    body = ChatBody(messages=[{"role": "user", "content": MAKE}], tools=["generate_media"])
    asyncio.run(run_chat_turn(platform, {}, body))
    assert "generate_media" in platform.router.tools[0]
    tools = _tools_section(platform.router.systems[0])
    assert NO_MEDIA_SETTINGS_LINE in tools and NO_MEDIA_CONNECT_LINE not in tools


def test_the_post_lane_says_nothing_when_a_service_is_connected(app):
    platform = app.state.platform
    platform.media = MediaRegistry([FakeProvider(enabled=True)], config=platform.config)
    platform.router = _PostModel([[]])
    body = ChatBody(messages=[{"role": "user", "content": MAKE}], tools=["generate_media"])
    asyncio.run(run_chat_turn(platform, {}, body))
    assert "NO MEDIA SERVICE" not in platform.router.systems[0]


def test_the_refusal_names_the_settings_place_never_a_vault_name_or_env_var(app, tmp_path):
    platform = app.state.platform
    tool = platform.registry.get("generate_media")
    ctx = _ctx(platform, tmp_path / "ws")
    for args in ({"kind": "image", "prompt": "a cat"}, {"kind": "image", "prompt": "a cat", "provider": "pixio"}):
        res = asyncio.run(tool.execute(args, ctx))
        assert not res.ok
        assert SETTINGS_PLACE in res.error, res.error
        for leak in ("PIXIO_API_KEY", "OPENAI_API_KEY", "secret 'pixio'", "IRONJARVIS_"):
            assert leak not in res.error, res.error
    for words in CONNECT_HINTS.values():
        assert not re.search(r"[A-Z][A-Z0-9]*_[A-Z0-9_]+", words), words
        assert "secret '" not in words


def test_the_swap_and_the_line_helpers():
    reg = SimpleNamespace(get=lambda name: object() if name == "app_connect" else None)
    assert swap_media_for_connect(["shell", "generate_media"], reg) == ["shell", "app_connect"]
    assert swap_media_for_connect(["app_connect", "generate_media"], reg) == ["app_connect"]
    assert swap_media_for_connect(["shell"], reg) == ["shell"]
    no_connect = SimpleNamespace(get=lambda name: None)
    assert swap_media_for_connect(["generate_media"], no_connect) == ["generate_media"]
    assert no_media_service_line(False, {"app_connect"}) == ""
    assert no_media_service_line(True, {"app_connect", "generate_media"}) == NO_MEDIA_CONNECT_LINE
    assert no_media_service_line(True, ["generate_media"]) == NO_MEDIA_SETTINGS_LINE
    assert no_media_service_line(True, {"read_file"}) == ""
    assert "app_connect" in NO_MEDIA_CONNECT_LINE and "pixio" in NO_MEDIA_CONNECT_LINE
    assert SETTINGS_PLACE in NO_MEDIA_SETTINGS_LINE


# =========================================================================== #
# G41 — a refusal leaves a button to the fix
# =========================================================================== #


def _failed(data: Any = None, error: str = "it failed") -> SimpleNamespace:
    return SimpleNamespace(ok=False, error=error, output=error, data=data)


def test_failure_door_for_names_the_fix_read_off_what_the_call_carried():
    assert failure_door_for("app_connect", {"app": "google_drive"}, _failed()) == DRIVE_DOOR
    for app_id in ("onedrive", "dropbox"):
        conn = get_connector(app_id)
        assert conn is not None and conn.connect_via == "oauth"
        assert failure_door_for("app_connect", {"app": app_id}, _failed()) == {
            "href": "/settings?section=connections-apps",
            "label": f"Connect {conn.name} in Settings",
        }
    # An app chat CAN connect (its own key card) or an unknown one: no door.
    for app_id in ("pixio", "notion", "github", "nope", ""):
        assert failure_door_for("app_connect", {"app": app_id}, _failed()) is None, app_id
    assert failure_door_for("app_connect", "not a dict", _failed()) is None
    # The browser's setup codes, and only those.
    for code in ("BROWSER_NOT_CONNECTED", "PAIRING_REQUIRED", "AUTHENTICATION_FAILED"):
        assert failure_door_for("browser_list_tabs", {}, _failed({"code": code})) == PAIR_DOOR
    assert failure_door_for("browser_read_page", {}, _failed({"code": "BROWSER_ACCESS_OFF"})) == {
        "href": "/computeruse", "label": "Turn on browser access",
    }
    assert failure_door_for("browser_click", {}, _failed({"code": "PERMISSION_DENIED"})) == {
        "href": "/computeruse", "label": "Grant site access",
    }
    for code in ("STALE_ELEMENT", "TAB_NOT_FOUND", "PAGE_FAILED_TO_LOAD", "", None):
        assert failure_door_for("browser_click", {}, _failed({"code": code})) is None
    # A code on a tool that is not a browser tool, a success, no data: none.
    assert failure_door_for("read_file", {}, _failed({"code": "BROWSER_NOT_CONNECTED"})) is None
    assert failure_door_for("browser_list_tabs", {}, SimpleNamespace(ok=True, data={"code": "BROWSER_NOT_CONNECTED"})) is None
    assert failure_door_for("app_connect", {"app": "google_drive"}, SimpleNamespace(ok=True, data={})) is None
    assert failure_door_for("browser_list_tabs", {}, _failed(None)) is None

    class _Bomb:
        @property
        def ok(self):
            raise RuntimeError("boom")

    assert failure_door_for("browser_list_tabs", {}, _Bomb()) is None


def test_fix_doors_are_page_level_and_share_the_cap():
    for door in (DRIVE_DOOR, PAIR_DOOR):
        path = door["href"].split("?", 1)[0]
        assert re.fullmatch(r"/[a-z-]+", path), door
    many = [DRIVE_DOOR, PAIR_DOOR, DRIVE_DOOR, None, {"href": "/a", "label": "a"}, {"href": "/b", "label": "b"}, {"href": "/c", "label": "c"}]
    assert collect_doors(many)[:2] == [DRIVE_DOOR, PAIR_DOOR] and len(collect_doors(many)) == 4


def test_connect_my_google_drive_leaves_a_connect_in_settings_button_stream_lane(app):
    """THE ACCEPTANCE: Drive is not set up; the reply ends with a button."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("connect my Google Drive so you can find my tax files"))
    assert "app_connect" in model.tools[0]
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False and "browser" in finished[0]["output"]
    assert _done(frames)["doors"] == [DRIVE_DOOR]
    _check_fixture("google_drive", _done(frames)["doors"])


def test_a_declined_connect_leaves_no_button(app, monkeypatch):
    # v1.353.0 integrate (moved on purpose): app_connect for an app that
    # signs in through the browser no longer shows an approval card (its
    # `spend_check` -- the call changes nothing; see the test below). The
    # LANE's rule this pins -- a call the user declined leaves no fix button
    # (`refusal_label` set) -- is kept by giving the tool its old card here.
    from iron_jarvis.settings.records import AppConnectTool

    async def _card(self, args, ctx):  # noqa: ANN001 — the pre-integrate verdict
        return None

    monkeypatch.setattr(AppConnectTool, "spend_check", _card)
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("connect my Google Drive"), answer="deny", cards=cards)
    assert cards, "anti-vacuity: the card was shown and declined"
    assert _done(frames)["doors"] == []


def test_connect_my_google_drive_asks_no_card_and_leaves_the_button(app):
    """v1.353.0 integrate: "connect my Google Drive" with Drive not set up
    shows NO "connect an app" card first -- an Allow for a call that can only
    answer "connect it in Settings" approves nothing (the v1.227.0 rule) --
    and the reply ends with the button to the fix."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    cards: list = []
    frames = _stream(app, _body("connect my Google Drive"), cards=cards)
    assert cards == []
    assert _done(frames)["doors"] == [DRIVE_DOOR]


def test_a_refusal_the_registry_made_leaves_no_button_stream_lane(app):
    """The fix door is for a call that FAILED because something is not set
    up, never for a refusal the registry made: here app_connect was not
    turned on for this chat ("not armed"), so the reply gets the footnote
    and no button. Review pin (v1.353.0 lane 3B)."""
    model = _StreamModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    app.state.platform.router.stream = model
    frames = _stream(app, _body("What can you do?"))
    assert "app_connect" not in model.tools[0], "anti-vacuity: app_connect was not armed"
    done = _done(frames)
    assert "app_connect could not run (not turned on for this chat)" in done["reply"], done["reply"]
    assert done["doors"] == []


def test_a_refusal_the_registry_made_leaves_no_button_post_lane(app):
    """The POST lane's twin (MIRROR NOTE, lock-step): a "not armed" refusal
    of app_connect for Drive leaves the footnote and no button."""
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    body = ChatBody(messages=[{"role": "user", "content": "connect my Google Drive"}], tools=["read_file"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert "app_connect" not in platform.router.tools[0], "anti-vacuity: app_connect was not armed"
    assert "app_connect could not run (not turned on for this chat)" in res["reply"], res["reply"]
    assert res["doors"] == []


def test_connect_my_google_drive_leaves_the_button_in_the_post_lane(app):
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="c1", name="app_connect", arguments={"app": "google_drive"})], []])
    body = ChatBody(messages=[{"role": "user", "content": "connect my Google Drive"}], tools=["app_connect"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert res["doors"] == [DRIVE_DOOR]
    assert "app_connect" not in res["tools_used"]


def test_an_unpaired_browser_refusal_shows_pair_your_browser_stream_lane(app):
    platform = app.state.platform
    platform.config.browser_access = "read_only"  # on, but no browser paired
    model = _StreamModel([[ToolCall(id="b1", name="browser_list_tabs", arguments={})], []])
    platform.router.stream = model
    frames = _stream(app, _body("what tabs do I have open in my browser?"))
    assert "browser_list_tabs" in model.tools[0]
    finished = [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished"]
    assert finished and finished[0]["ok"] is False and "BROWSER_NOT_CONNECTED" in finished[0]["output"]
    assert _done(frames)["doors"] == [PAIR_DOOR]
    _check_fixture("unpaired_browser", _done(frames)["doors"])


def test_an_unpaired_browser_refusal_shows_pair_your_browser_post_lane(app):
    platform = app.state.platform
    platform.config.browser_access = "read_only"
    platform.router = _PostModel([[ToolCall(id="b1", name="browser_list_tabs", arguments={})], []])
    body = ChatBody(messages=[{"role": "user", "content": "list my browser tabs"}], tools=["browser_list_tabs"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert res["doors"] == [PAIR_DOOR]


def test_an_ordinary_failure_still_opens_no_door(app):
    """The v1.199.0 honesty gate holds: a failed create is not a door."""
    platform = app.state.platform
    platform.router = _PostModel([[ToolCall(id="w1", name="workflow_create", arguments={"steps": []})], []])
    body = ChatBody(messages=[{"role": "user", "content": "save it"}], tools=["workflow_create"])
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert res["doors"] == []


def test_an_app_tools_refusal_reads_in_the_users_words():
    out = refusal_footnotes([("mcp__github__create_issue", "not armed")])
    assert out == (
        "\n\n_Note: GitHub › create issue could not run (not turned on for this chat)"
        " — turn GitHub on in + › Connections._"
    )
    assert "mcp__" not in out
    denied = refusal_footnotes([("mcp__github__create_issue", "permission denied")])
    assert denied == "\n\n_Note: GitHub › create issue could not run (permission denied)._"
    # A pack the user added by hand keeps its own name; the tool reads as words.
    assert app_tool_words("mcp__my-pack__listOpenIssues") == ("my-pack", "list open issues")
    # A catalog app is named by the catalog; an acronym keeps its case.
    assert app_tool_words("mcp__github__get_PR") == (get_connector("github").name, "get PR")
    # Built-ins are byte for byte the v1.344.0 words; malformed ids stay raw.
    assert refusal_footnotes([("image_info", "permission denied")]) == (
        "\n\n_Note: image_info could not run (permission denied)._"
    )
    for raw in ("mcp__", "mcp____x", "mcp__ok__", "mcp__bad pack__x"):
        assert app_tool_words(raw) is None, raw
    assert refusal_footnotes([("mcp__", "not armed")]) == "\n\n_Note: mcp__ could not run (not turned on for this chat)._"


class _GitHubTool(Tool):
    """A connected GitHub pack's tool as the registry holds one
    (``mcp__<pack>__<tool>``) — registered, but not turned on for this chat."""

    name = "mcp__github__create_issue"
    description = "Create an issue."
    input_schema = {"type": "object", "properties": {"title": {"type": "string"}}}

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(ok=True, output="made")


def test_an_unarmed_app_tool_in_a_real_turn_is_footnoted_in_app_words(app):
    """Through the real registry gate: GitHub is connected, but its switch
    is off for this chat; the model calls its tool anyway. The registry
    refuses it ("not armed") and the reply's footnote names GitHub and where
    to turn it on — never ``mcp__github__create_issue``."""
    github = _GitHubTool()
    app.state.platform.registry.register(github)
    model = _StreamModel([[ToolCall(id="m1", name="mcp__github__create_issue", arguments={"title": "x"})], []])
    app.state.platform.router.stream = model
    # A sentence that arms something, so the turn has a tool loop at all.
    frames = _stream(app, _body("What can you do?"))
    assert "mcp__github__create_issue" not in model.tools[0]
    reply = _done(frames)["reply"]
    assert github.calls == [], "anti-vacuity: the unarmed tool never ran"
    assert "GitHub › create issue could not run (not turned on for this chat)" in reply, reply
    assert "turn GitHub on in + › Connections" in reply
    assert "mcp__github__create_issue could not run" not in reply


# =========================================================================== #
# lock-step: both lanes carry the same seams (CRLF tolerant)
# =========================================================================== #


def test_both_lanes_carry_the_fix_door_and_the_media_line_lock_step():
    for module in (chat_turn, chat_routes):
        src = inspect.getsource(module).replace("\r\n", "\n")
        where = module.__name__
        needle = "door_entries.append(failure_door_for(tc.name, tc.arguments, result))"
        assert src.count(needle) == 1, where
        at = src.index(needle)
        gate = src.rindex("refused_tools.append((tc.name, _label))", 0, at)
        assert at - gate < 800, f"{where}: the fix door drifted from the refusal site"
        assert src.count("no_media_service_line(_media_unready,") == 1, where
        assert src.count("_media_unready = False") == 1, where
        assert src.count("_media_unready = True") == 1, where
    # The swap is the stream lane's alone: the POST lane never ask-arms.
    routes_src = inspect.getsource(chat_routes)
    assert routes_src.count("swap_media_for_connect(") == 1
    assert "swap_media_for_connect(" not in inspect.getsource(chat_turn.run_chat_turn)
