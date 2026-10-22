"""v1.371.0 — the IJ harness can SEE: pictures in a tool result, and
``page_screenshot`` of the page the Browser pane shows.

The user asked whether "sending images or viewing browser preview within the
IJ harness" work; neither did. The screen had no attach; the Browser pane was
for the user only (no tool looked at it, the paired browser is not armed in a
Build session, web_fetch refuses local addresses on purpose). These pins hold
the server half:

* :mod:`iron_jarvis.harness.preview` — which addresses count as "this PC or
  your network", and the Browser pane's address for the turn;
* ``page_screenshot`` — looks at the pane's page without asking, asks for any
  other local page, refuses public sites, and hands the PICTURE back;
* both chat lanes put a tool's pictures on the ``role="tool"`` message and
  keep only the newest two (lock-step);
* Claude (CLI and API) and Codex carry the picture inside the tool result.
"""

from __future__ import annotations

import base64
import json
import sys
import textwrap
from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import page_shot as PS
from iron_jarvis.harness import preview as PV
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage, LLMResponse, ToolCall
from iron_jarvis.providers.adapters.claude_native import frames as CF
from iron_jarvis.providers.adapters.codex_native import protocol as CP
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolResult

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PIC = {"media_type": "image/png", "data_b64": base64.b64encode(PNG_1X1).decode()}


# --------------------------------------------------------------------------- #
# Which addresses are "this PC or your network"
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "url, kept",
    [
        ("http://localhost:5173/settings", "http://localhost:5173/settings"),
        ("http://127.0.0.1:3000", "http://127.0.0.1:3000"),
        ("http://0.0.0.0:3000/app", "http://localhost:3000/app"),
        ("http://192.168.1.20:8080/", "http://192.168.1.20:8080/"),
        ("http://spark1:4000", "http://spark1:4000"),
        ("https://example.com", ""),
        ("http://8.8.8.8", ""),
        ("file:///C:/secret.txt", ""),
        ("http://user:pw@localhost:5173", ""),
        ("javascript:alert(1)", ""),
        ("", ""),
        # v1.371.0 review: a browser PERCENT-DECODES a host (URL Standard), so
        # "evil%2ecom" opens evil.com — while the egress check cut the name at
        # its first "%" (an IPv6 zone id's mark) and judged a LAN name "evil".
        ("http://evil%2ecom/", ""),
        ("http://evil%E3%80%82com:8080/", ""),
        ("http://[fe80::1%25eth0]:3000/", "http://[fe80::1%25eth0]:3000/"),
    ],
)
def test_only_an_address_on_this_pc_or_network_is_kept(url, kept):
    assert PV.local_address(url) == kept


def test_a_percent_in_a_host_name_is_not_a_zone_id():
    """The ONE definition of "on this network" (egress.private_host) — Local
    only's endpoint check reads it too."""
    from iron_jarvis.providers.egress import private_host

    assert not private_host("http://api%2eopenai%2ecom/v1")
    assert private_host("http://[fe80::1%25eth0]:8000")  # a real zone id still reads local


def test_one_dev_server_is_one_origin_whatever_page_is_open():
    assert PV.origin("http://localhost:5173/a") == PV.origin("http://127.0.0.1:5173/b?x=1")
    assert PV.origin("http://localhost:5173/") != PV.origin("http://localhost:5174/")
    assert PV.origin("https://example.com") == ""


def test_the_prompt_names_the_pane_only_when_the_tool_is_armed():
    token = PV.set_preview("http://localhost:5173/")
    try:
        line = PV.prompt_line({"read_file", "page_screenshot"})
        assert "http://localhost:5173/" in line and "page_screenshot" in line
        assert PV.prompt_line({"read_file"}) == ""
    finally:
        PV.reset_preview(token)
    assert PV.prompt_line({"page_screenshot"}) == ""


def test_a_public_address_never_becomes_the_preview():
    token = PV.set_preview("https://example.com/")
    try:
        assert PV.current_preview() == ""
    finally:
        PV.reset_preview(token)


# --------------------------------------------------------------------------- #
# page_screenshot
# --------------------------------------------------------------------------- #
class _Ctx:
    def __init__(self, home: Path) -> None:
        self.config = type("C", (), {"home": home})()


async def test_the_panes_page_runs_without_a_card_and_any_other_asks():
    tool = PS.PageScreenshotTool()
    token = PV.set_preview("http://localhost:5173/")
    try:
        same = await tool.spend_check({"url": "http://127.0.0.1:5173/other"}, None)
        assert same is not None and same.ask is False
        other = await tool.spend_check({"url": "http://localhost:8765/"}, None)
        assert other is not None and other.ask is True
        assert "http://localhost:8765/" in other.words and "Browser" in other.words
        assert "pane" not in other.words  # a retired word on a card (v13600 scan)
        assert await tool.spend_check({"url": "https://example.com"}, None) is None
    finally:
        PV.reset_preview(token)
    # With no pane address, every local page asks.
    none = await tool.spend_check({"url": "http://localhost:5173/"}, None)
    assert none is not None and none.ask is True


async def test_a_public_site_is_refused_in_words(tmp_path):
    res = await PS.PageScreenshotTool().execute({"url": "https://example.com"}, _Ctx(tmp_path))
    assert res.ok is False and "does not open public websites" in res.error


async def test_no_browser_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(PS, "browser_executable", lambda: "")
    res = await PS.PageScreenshotTool().execute({"url": "http://localhost:5173"}, _Ctx(tmp_path))
    assert res.ok is False and res.error == PS.NO_BROWSER


def _fake_browser(tmp_path: Path, *, writes: bool) -> Path:
    script = tmp_path / "fake_browser.py"
    body = f"""
        import sys, base64
        out = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--screenshot="))
        if {writes!r}:
            open(out, "wb").write(base64.b64decode({base64.b64encode(PNG_1X1).decode()!r}))
        else:
            sys.stderr.write("could not load the page\\n")
    """
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


async def test_a_screenshot_comes_back_as_a_picture_and_a_saved_file(tmp_path, monkeypatch):
    script = _fake_browser(tmp_path, writes=True)
    monkeypatch.setattr(PS, "browser_executable", lambda: sys.executable)
    seen: dict = {}

    def argv(binary, url, out, profile, w, h, ms):
        seen.update(url=url, w=w, h=h, ms=ms, profile=profile)
        return [sys.executable, str(script), f"--screenshot={out}", url]

    monkeypatch.setattr(PS, "browser_argv", argv)
    res = await PS.PageScreenshotTool().execute(
        {"url": "http://0.0.0.0:5173/", "width": 99999, "wait_ms": -5}, _Ctx(tmp_path / "home"),
    )
    assert res.ok, res.error
    assert seen["url"] == "http://localhost:5173/"  # 0.0.0.0 is this PC
    assert seen["w"] == PS.MAX_WIDTH and seen["ms"] == 0  # clamped
    assert not Path(seen["profile"]).exists()  # the throwaway profile is gone
    saved = Path(res.data["file"])
    assert saved.is_file() and saved.parent == tmp_path / "home" / "page-screenshots"
    assert res.images == [{"media_type": "image/png", "data_b64": base64.b64encode(PNG_1X1).decode()}]
    assert "picture is attached" in res.output and str(saved) in res.output
    assert "view_image" in res.output  # the way for a model that cannot see it


async def test_a_browser_that_wrote_nothing_is_a_failure_with_its_words(tmp_path, monkeypatch):
    script = _fake_browser(tmp_path, writes=False)
    monkeypatch.setattr(PS, "browser_executable", lambda: sys.executable)
    monkeypatch.setattr(PS, "browser_argv",
                        lambda b, u, o, p, w, h, ms: [sys.executable, str(script), f"--screenshot={o}", u])
    res = await PS.PageScreenshotTool().execute({"url": "http://localhost:5173"}, _Ctx(tmp_path))
    assert res.ok is False and "wrote no picture" in res.error and "could not load the page" in res.error


def test_the_browser_runs_headless_on_a_throwaway_profile(tmp_path):
    argv = PS.browser_argv("edge.exe", "http://localhost:5173", tmp_path / "o.png", tmp_path / "p",
                           1280, 800, 1500)
    assert "--headless=new" in argv and "--disable-extensions" in argv
    assert f"--user-data-dir={tmp_path / 'p'}" in argv
    assert argv[-1] == "http://localhost:5173"


def test_old_screenshots_are_pruned(tmp_path):
    for i in range(PS.KEEP_SHOTS + 5):
        (tmp_path / f"page-{i:03d}.png").write_bytes(b"x")
    PS._prune(tmp_path)
    assert len(list(tmp_path.glob("page-*.png"))) == PS.KEEP_SHOTS


# --------------------------------------------------------------------------- #
# Lanes: pictures on the tool message, newest two kept
# --------------------------------------------------------------------------- #
def test_only_a_successful_result_hands_pictures_on():
    ok = ToolResult(ok=True, output="x", images=[PIC, {"media_type": "text/plain", "data_b64": "eA=="}])
    assert chat_turn.tool_result_images(ok, True) == [PIC]
    assert chat_turn.tool_result_images(ok, False) == []
    assert chat_turn.tool_result_images(None, True) == []
    many = ToolResult(ok=True, images=[PIC] * 9)
    assert len(chat_turn.tool_result_images(many, True)) == chat_turn.MAX_TOOL_IMAGES


def test_only_the_newest_two_results_keep_their_pictures():
    msgs = [LLMMessage(role="tool", content=f"r{i}", images=[PIC]) for i in range(4)]
    chat_turn.trim_tool_images(msgs)
    assert [bool(m.images) for m in msgs] == [False, False, True, True]
    assert chat_turn.DROPPED_PICTURE_NOTE in msgs[0].content
    assert chat_turn.DROPPED_PICTURE_NOTE not in msgs[3].content


class _Model:
    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []

    def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "messages": list(messages)})
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})

    async def stream(self, *, system, messages, tools, **kw):
        resp = self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "anthropic", "model": "claude-sonnet-4-5"}

    async def complete(self, *, system, messages, tools, **kw):
        return RouteResult(self._next(system, messages, tools), "anthropic", "claude-sonnet-4-5")


@pytest.fixture()
def lane(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()

    def wire(moves):
        model = _Model(moves)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, text: str, mode: str = "approve_for_me") -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider="anthropic", model="claude-sonnet-4-5", workspace_dir=str(ws), approval_mode=mode,
    )


async def test_the_stream_lane_shows_the_model_the_panes_page(lane, monkeypatch):
    platform, ws, wire = lane

    async def fake_execute(self, args, ctx):
        return ToolResult(ok=True, output="Screenshot of the page; the picture is attached.",
                          images=[PIC])

    monkeypatch.setattr(PS.PageScreenshotTool, "execute", fake_execute)
    model = wire([[ToolCall(id="c1", name="page_screenshot", arguments={"url": "http://localhost:5173/"})]])
    token = PV.set_preview("http://localhost:5173/")
    try:
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(ws, "how does my page look?"),
                                     harness="auto")
        chunks = [c async for c in gen]
    finally:
        PV.reset_preview(token)
    text = "".join(chunks)
    # The pane's page ran WITHOUT a card, even under approve-for-me.
    assert "event: approval" not in text, text[-600:]
    # The prompt named the pane's page...
    assert "Browser pane is showing http://localhost:5173/" in model.calls[0]["system"]
    # ...and the model's next call carried the picture on the tool result.
    tool_msgs = [m for m in model.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and tool_msgs[-1].images == [PIC]


async def test_the_post_lane_carries_a_tools_pictures_too(lane, monkeypatch):
    """Lock-step: the POST lane arms no ask-tier tool, so the pin uses an
    allow-tier harness tool whose result carries a picture."""
    platform, ws, wire = lane
    from iron_jarvis.harness.tools import GlobTool

    async def fake_glob(self, args, ctx):
        return ToolResult(ok=True, output="found a.png", images=[PIC])

    monkeypatch.setattr(GlobTool, "execute", fake_glob)
    model = wire([[ToolCall(id="g1", name="glob", arguments={"pattern": "*.png"})]])
    await run_chat_turn(platform, BUILTIN_PERSONAS, _body(ws, "find the png", "yolo"), harness="auto")
    tool_msgs = [m for m in model.calls[1]["messages"] if m.role == "tool"]
    assert tool_msgs and tool_msgs[-1].images == [PIC]


# --------------------------------------------------------------------------- #
# Adapters: the picture travels INSIDE the tool result
# --------------------------------------------------------------------------- #
def _tool_turn() -> list[LLMMessage]:
    return [
        LLMMessage(role="user", content="look"),
        LLMMessage(role="assistant", content="", tool_calls=[ToolCall(id="t1", name="page_screenshot",
                                                                         arguments={"url": "http://localhost:1"})]),
        LLMMessage(role="tool", tool_call_id="t1", name="page_screenshot", content="shot", images=[PIC]),
    ]


def test_claude_cli_puts_the_picture_in_the_tool_result():
    names = CF.ToolNames.build(["page_screenshot"])
    _system, frames = CF.history_frames("sys", _tool_turn(), names)
    result = frames[-1]["message"]["content"][0]
    assert result["type"] == "tool_result"
    assert result["content"][0] == {"type": "text", "text": "shot"}
    assert result["content"][1] == {
        "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIC["data_b64"]},
    }


def test_claude_cli_keeps_a_plain_result_a_string():
    msgs = _tool_turn()
    msgs[-1].images = []
    _system, frames = CF.history_frames("sys", msgs, CF.ToolNames.build(["page_screenshot"]))
    assert frames[-1]["message"]["content"][0]["content"] == "shot"


def test_the_claude_api_puts_the_picture_in_the_tool_result():
    out = AnthropicAdapter._to_anthropic_messages(_tool_turn())
    content = out[-1]["content"][0]["content"]
    assert content[0] == {"type": "text", "text": "shot"} and content[1]["type"] == "image"


def test_codex_sends_the_picture_as_an_input_image():
    resp = CP.tool_response("shot", [PIC])
    assert resp["contentItems"] == [
        {"type": "inputText", "text": "shot"},
        {"type": "inputImage", "imageUrl": "data:image/png;base64," + PIC["data_b64"]},
    ]
    assert CP.tool_response("shot") == {"contentItems": [{"type": "inputText", "text": "shot"}], "success": True}
    assert CP.answered_images(_tool_turn()) == {"t1": [PIC]}


def test_a_replayed_codex_history_says_a_picture_was_left_out():
    text = CP._render(_tool_turn(), CP.ToolNames.build(["page_screenshot"]))
    assert "1 picture(s) were attached to this result; not replayed" in text
    json.dumps(text)


# --------------------------------------------------------------------------- #
# The Build route: the pane's address and a picture-only message
# --------------------------------------------------------------------------- #
from iron_jarvis.daemon.app import create_app  # noqa: E402
from iron_jarvis.daemon.routes import build as build_routes  # noqa: E402
from tests.test_build_sessions_v1367 import (  # noqa: E402
    Script,
    frames_of,
    new_session,
    open_turn,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


def _spy_preview(monkeypatch) -> list[str]:
    """What the turn's lane saw as the Browser pane's address (the ContextVar
    while the lane was being built), and that it was gone afterwards."""
    seen: list[str] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(PV.current_preview())
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


async def test_the_turn_carries_the_panes_address_into_the_prompt(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_preview(monkeypatch)
    script = wire(app, Script(["All good."]))
    status, raw = await open_turn(app, sid, {"text": "how does it look?", "turn_id": "bt-p1",
                                             "preview_url": "http://localhost:5173/cart"})
    assert status == 200, raw
    assert seen == ["http://localhost:5173/cart"]
    assert PV.current_preview() == ""  # reset after the turn was started
    assert "Browser pane is showing http://localhost:5173/cart" in script.calls[0]["system"]


async def test_a_public_preview_address_is_dropped(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_preview(monkeypatch)
    script = wire(app, Script(["Hi."]))
    status, raw = await open_turn(app, sid, {"text": "hello", "turn_id": "bt-p2",
                                             "preview_url": "https://example.com/"})
    assert status == 200, raw
    assert seen == [""]
    assert "Browser pane is showing" not in script.calls[0]["system"]


async def test_a_picture_alone_is_a_message_but_nothing_at_all_is_not(app, work, tmp_path):
    sid = new_session(app, work)["id"]
    shot = tmp_path / "home" / "uploads" / "shot.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(PNG_1X1)
    script = wire(app, Script(["I see the picture."]))
    status, raw = await open_turn(app, sid, {"text": "", "turn_id": "bt-p3", "attachments": [str(shot)]})
    assert status == 200, raw
    assert frames_of(raw)[-1][0] == "done"
    # The model was handed the picture itself on the question.
    asked = [m for m in script.calls[0]["messages"] if getattr(m, "role", "") == "user"][-1]
    assert asked.images and asked.images[0]["media_type"] == "image/png"
    status, raw = await open_turn(app, sid, {"text": "  ", "turn_id": "bt-p4"})
    assert status == 400 and "Write a message first." in raw


def test_codex_says_it_takes_pictures_so_a_screenshot_does_not_move_the_turn(monkeypatch):
    """The live run (2026-10-22): a Codex Build session took a page_screenshot
    and the NEXT call was answered by Claude — the router's vision check read
    codex-cli's capabilities ("vision": False) and moved the turn. The
    app-server takes pictures (user items and tool results)."""
    from iron_jarvis.providers.adapters.codex_native import session as CS
    from iron_jarvis.providers.adapters.codex_native.adapter import CodexNativeAdapter

    adapter = CodexNativeAdapter.__new__(CodexNativeAdapter)
    adapter.provider, adapter.model = "codex-cli", "subscription"
    monkeypatch.setattr(CS, "verdict", lambda: None)  # the native path is on
    assert adapter.capabilities()["vision"] is True

    class _No:
        ok = False

        def expired(self):
            return False

    monkeypatch.setattr(CS, "verdict", lambda: _No())  # the text-only fallback
    assert adapter.capabilities()["vision"] is False


# --------------------------------------------------------------------------- #
# Review (v1.371.0): where the browser runs, Stop, Local only, a picture alone
# --------------------------------------------------------------------------- #
def _recording_browser(tmp_path: Path, *, sleep_s: float = 0.0) -> Path:
    """A stand-in browser that takes the REAL command line (browser_argv's
    flags), writes down where it was told to keep its profile and picture
    and what the profile's preferences said, then (after ``sleep_s``) writes
    the picture."""
    script = tmp_path / "recording_browser.py"
    seen = tmp_path / "seen.json"
    body = f"""
        import json, os, sys, time, base64, pathlib
        flags = dict(a.split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
        prof = pathlib.Path(flags["--user-data-dir"])
        prefs = json.loads((prof / "Default" / "Preferences").read_text(encoding="utf-8"))
        pathlib.Path({str(seen)!r}).write_text(json.dumps({{
            "profile": str(prof), "shot": flags["--screenshot"], "prefs": prefs, "pid": os.getpid(),
        }}), encoding="utf-8")
        time.sleep({sleep_s!r})
        open(flags["--screenshot"], "wb").write(base64.b64decode({base64.b64encode(PNG_1X1).decode()!r}))
    """
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def _use(monkeypatch, script: Path) -> None:
    real = PS.browser_argv
    monkeypatch.setattr(PS, "browser_executable", lambda: sys.executable)
    monkeypatch.setattr(
        PS, "browser_argv",
        lambda b, u, o, p, w, h, ms: [sys.executable, str(script), *real(b, u, o, p, w, h, ms)[1:]],
    )


async def test_the_browser_works_outside_the_home_and_a_download_stays_in_its_folder(tmp_path, monkeypatch):
    """A backup walks the whole home and fails on a file it cannot read; a
    running browser locks ~60 files of its profile (measured with Edge). And
    a page can start a download with no click, which a fresh profile saves to
    the user's own Downloads folder (measured). So the profile, the picture
    being written and any download all live in a throwaway folder OUTSIDE the
    home, removed after; only the finished picture moves in."""
    script = _recording_browser(tmp_path)
    _use(monkeypatch, script)
    home = tmp_path / "home"
    res = await PS.PageScreenshotTool().execute({"url": "http://localhost:5173"}, _Ctx(home))
    assert res.ok, res.error
    seen = json.loads((tmp_path / "seen.json").read_text(encoding="utf-8"))
    profile, shot = Path(seen["profile"]), Path(seen["shot"])
    for p in (profile, shot):
        assert home not in p.parents, f"{p} is inside the home a backup walks"
    work = profile.parent
    assert shot.parent == work
    downloads = Path(seen["prefs"]["download"]["default_directory"])
    assert downloads.parent == work  # never the user's Downloads folder
    assert seen["prefs"]["download"]["prompt_for_download"] is False
    assert not work.exists()  # the throwaway folder is gone
    assert Path(res.data["file"]).parent == home / "page-screenshots"


def _wait_dead(pid: int, timeout: float) -> bool:
    import psutil

    try:
        psutil.Process(pid).wait(timeout=timeout)
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.TimeoutExpired:
        return False


async def test_stop_kills_the_browser_and_removes_its_folder(tmp_path, monkeypatch):
    """A cancelled ``to_thread`` does not stop its thread (the v1.228.0 trap):
    a Stop used to leave the browser running to its own 45 s bound."""
    import asyncio

    import psutil

    script = _recording_browser(tmp_path, sleep_s=30.0)
    _use(monkeypatch, script)
    seen_file = tmp_path / "seen.json"
    task = asyncio.ensure_future(
        PS.PageScreenshotTool().execute({"url": "http://localhost:5173"}, _Ctx(tmp_path / "home"))
    )
    for _ in range(300):  # the stand-in started (≤ 30 s on a loaded runner)
        if seen_file.exists():
            break
        await asyncio.sleep(0.1)
    seen = json.loads(seen_file.read_text(encoding="utf-8"))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    try:
        assert await asyncio.to_thread(_wait_dead, seen["pid"], 15.0), "the browser outlived the Stop"
    finally:
        try:
            psutil.Process(seen["pid"]).kill()
        except psutil.Error:
            pass
    work = Path(seen["profile"]).parent
    for _ in range(100):
        if not work.exists():
            break
        await asyncio.sleep(0.1)
    assert not work.exists()


def test_a_local_only_session_asks_before_every_screenshot(tmp_path):
    """The page is on this PC, but it loads what it names — and a page the
    model wrote can name an outside address carrying anything. Like computer
    use's isolated browser, it cards in a local-only turn, the pane's page
    included, and the prompt line does not promise otherwise."""
    from iron_jarvis.providers import egress

    platform = build_platform(str(tmp_path / "home"))
    assert egress.reaches_outside(PS.PageScreenshotTool(), platform.registry)
    with egress.local_only_scope(True):
        assert egress.asks_first(platform.registry, "page_screenshot")
        token = PV.set_preview("http://localhost:5173/")
        try:
            line = PV.prompt_line({"page_screenshot"})
        finally:
            PV.reset_preview(token)
    assert "without asking" not in line and "asks the user first" in line
    assert not egress.asks_first(platform.registry, "page_screenshot")  # scope off


def test_a_picture_sent_alone_has_no_empty_text_block_for_the_claude_api():
    """The Messages API refuses an empty text block; a picture alone is now a
    message in the IJ harness (and has been in chat since v1.275.0)."""
    out = AnthropicAdapter._to_anthropic_messages([LLMMessage(role="user", content="", images=[PIC])])
    assert [b["type"] for b in out[0]["content"]] == ["image"]
    out = AnthropicAdapter._to_anthropic_messages([LLMMessage(role="user", content="look", images=[PIC])])
    assert [b["type"] for b in out[0]["content"]] == ["text", "image"]


async def test_codexs_text_only_fallback_says_the_pictures_were_not_shown(monkeypatch):
    """Vision is claimed for the native path; when this call fell back to
    ``codex exec`` the pictures were flattened away without a word."""
    from iron_jarvis.providers.adapters import subprocess_cli
    from iron_jarvis.providers.adapters.codex_native.adapter import CodexNativeAdapter

    seen: list[str] = []

    class _Text:
        async def stream(self, *, system, messages, tools, **kw):
            seen.append(system)
            yield {"type": "final", "response": LLMResponse(text="ok")}

    monkeypatch.setattr(subprocess_cli, "make_codex_cli", lambda **kw: _Text())
    adapter = CodexNativeAdapter(which=lambda _b: "codex")
    with_pic = [LLMMessage(role="user", content="", images=[PIC, PIC])]
    _ = [f async for f in adapter._fallback("sys", with_pic, [], "")]
    assert "2 picture(s)" in seen[-1] and "cannot show them" in seen[-1]
    _ = [f async for f in adapter._fallback("sys", [LLMMessage(role="user", content="hi")], [], "")]
    assert seen[-1] == "sys"  # no picture, the prompt is exactly as before


async def test_a_picture_sent_alone_is_still_a_question_in_the_next_turns_history(app, work, tmp_path):
    """The history kept role + content and dropped an empty question — so
    after a picture sent alone, the next turn's conversation STARTED with the
    assistant's reply to a question that was not there, and the model never
    learned a picture had been sent."""
    import asyncio

    sid = new_session(app, work)["id"]
    shot = tmp_path / "home" / "uploads" / "cart.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    shot.write_bytes(PNG_1X1)
    script = wire(app, Script(["I see the cart.", "Matched it."]))
    status, raw = await open_turn(app, sid, {"text": "", "turn_id": "bt-h1", "attachments": [str(shot)]})
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw
    for _ in range(100):  # the first turn has let go of the session
        status, raw = await open_turn(app, sid, {"text": "Make the button match it", "turn_id": "bt-h2"})
        if status != 409:
            break
        await asyncio.sleep(0.05)
    assert status == 200, raw
    convo = [m for m in script.calls[-1]["messages"] if m.role in ("user", "assistant")]
    assert [m.role for m in convo] == ["user", "assistant", "user"], [(m.role, m.content) for m in convo]
    assert "cart.png" in convo[0].content and str(shot.parent) not in convo[0].content
    assert convo[1].content == "I see the cart."


async def test_a_tools_picture_never_moves_the_turn_to_another_provider():
    """The router's vision reroute read EVERY message's pictures, so the
    first page_screenshot on a model without vision (a local fleet model, a
    text-only CLI) moved the rest of the turn — the user's code and files
    with it — to a vision provider the user never picked. A tool's picture is
    an extra the text already explains ("If you cannot see a picture here,
    call view_image"); only a picture the USER sent may ask for vision."""
    from iron_jarvis.core.events import EventBus
    from iron_jarvis.providers.adapters.base import LLMAdapter
    from iron_jarvis.providers.manager import ProviderManager
    from iron_jarvis.providers.router import ModelRouter

    class _Plain(LLMAdapter):
        def __init__(self, provider: str, vision: bool):
            self.provider, self.model, self._vision, self.calls = provider, "m", vision, 0

        def capabilities(self):
            return {"provider": self.provider, "model": self.model, "tool_use": True, "vision": self._vision}

        async def complete(self, *, system, messages, tools, **kw):
            self.calls += 1
            return LLMResponse(text=f"{self.provider} answered")

    local, vision = _Plain("fleet-local", False), _Plain("anthropic", True)
    manager = ProviderManager(credential_resolver=lambda n: "key" if n == "anthropic" else None)
    manager.register("fleet-local", lambda model=None: local)
    manager.register("anthropic", lambda model=None: vision)
    router = ModelRouter(manager, "anthropic", EventBus())
    tools = [{"name": "page_screenshot", "description": "d", "input_schema": {"type": "object"}}]
    res = await router.complete(provider="fleet-local", system="", messages=_tool_turn(), tools=tools)
    assert res.provider == "fleet-local" and local.calls == 1 and vision.calls == 0
    # A picture the USER sent still asks for a model that can see it.
    asked = [LLMMessage(role="user", content="what is this?", images=[PIC])]
    res = await router.complete(provider="fleet-local", system="", messages=asked, tools=tools)
    assert res.provider == "anthropic"


async def test_a_picture_too_big_for_any_model_is_named_not_attached(tmp_path, monkeypatch):
    script = _fake_browser(tmp_path, writes=True)
    monkeypatch.setattr(PS, "browser_executable", lambda: sys.executable)
    monkeypatch.setattr(PS, "browser_argv",
                        lambda b, u, o, p, w, h, ms: [sys.executable, str(script), f"--screenshot={o}", u])
    monkeypatch.setattr(PS, "_fit", lambda raw: (b"x" * (PS.HARD_IMAGE_BYTES + 1), "image/png"))
    res = await PS.PageScreenshotTool().execute({"url": "http://localhost:5173"}, _Ctx(tmp_path / "home"))
    assert res.ok and not res.images
    assert "too large to attach" in res.output and res.data["attached"] is False


async def test_an_overlong_pane_address_never_fails_the_message(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_preview(monkeypatch)
    wire(app, Script(["Hi."]))
    status, raw = await open_turn(app, sid, {"text": "hello", "turn_id": "bt-p5",
                                             "preview_url": "http://localhost:5173/" + "a" * 5000})
    assert status == 200, raw
    assert seen == [""]
