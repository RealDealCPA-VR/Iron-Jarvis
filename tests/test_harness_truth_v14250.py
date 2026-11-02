"""v1.425.0 (Wave 5, lane B) — the model SEES what it reads, and the tool
words tell the truth.

C4. An image a coding session's model READS (Claude Code's ``Read`` on a
.png, Qwen Code's ``read_file``, Codex's ``view_image``) came back as another
model's DESCRIPTION — the native tools hand their model the picture. Now the
picture comes back inside the tool result when the round's serving model takes
one there (``carries_tool_pictures``: claude-cli, the Anthropic API, Codex's
app-server), bounded like ``page_screenshot`` (re-encoded and shrunk past
``page_shot.MAX_IMAGE_BYTES``, never attached past ``HARD_IMAGE_BYTES``, only
the newest two results keep theirs); otherwise it is described as before and
the result SAYS the model cannot see pictures here. The turn never moves.

C5. Every sentence the coding-session dialects show was read against the code:
the approval sentence is true per posture, Bash and Qwen's command tool wait
2 minutes (Claude Code's default) when no timeout is given and say what happens
at it, a windowed Read stops at a whole line inside the turn's limit for one
tool result and says which lines it showed, a pass-through description never
points at a tool the turn cannot reach, the dialect's advice names only tools
it shows, and a command tool says when this reply cannot keep a program
running.

C6. "What I did in that reply" rides the newest reply THAT RAN TOOLS (and is
shown), or nothing.

The lane pins drive the REAL stream (and POST) lane with a scripted model on
the router, the way ``test_harness_pictures_v1371`` does.
"""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import dialects as D
from iron_jarvis.harness import mode as M
from iron_jarvis.harness import page_shot as PS
from iron_jarvis.harness import sessions as S
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse, ToolCall, carries_tool_pictures
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.sandbox.shell_tool import SandboxedShellTool
from iron_jarvis.tools import builtins as B
from iron_jarvis.tools import images as I
from iron_jarvis.tools.base import ToolResult

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
CLAUDE = ("anthropic", "claude-sonnet-4-5")
LOCAL = ("custom", "mystery-7b")


def _noise_png(side: int) -> bytes:
    """A picture that does not compress (random pixels)."""
    import os

    from PIL import Image

    img = Image.frombytes("RGB", (side, side), os.urandom(side * side * 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# a scripted model on the router (what each round was shown is kept)
# --------------------------------------------------------------------------- #
class _Model:
    def __init__(self, moves: list, who: tuple[str, str] = CLAUDE) -> None:
        self.moves = list(moves)
        self.who = who
        self.calls: list[dict] = []
        self.side_calls: list[dict] = []

    def _next(self, system, messages, tools, kw) -> LLMResponse:
        if not tools:
            # a call made BY a tool (view_image describing a picture)
            self.side_calls.append({"messages": list(messages), "kw": dict(kw)})
            return LLMResponse(text="A red square on white.", usage={"input_tokens": 1, "output_tokens": 1})
        self.calls.append({"system": system, "messages": list(messages), "tools": list(tools), "kw": dict(kw)})
        move = self.moves.pop(0) if self.moves else "Done."
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})

    async def stream(self, *, system, messages, tools, **kw):
        resp = self._next(system, messages, tools, kw)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": self.who[0], "model": self.who[1]}

    async def complete(self, *, system, messages, tools, **kw):
        return RouteResult(self._next(system, messages, tools, kw), *self.who)


@pytest.fixture()
def lane(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()

    def wire(moves, who=CLAUDE):
        model = _Model(moves, who)
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return platform, ws, wire


def _body(ws: Path, text: str, who=CLAUDE, mode: str = "approve_for_me") -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider=who[0], model=who[1], workspace_dir=str(ws), approval_mode=mode,
    )


async def _stream(platform, body, **kw) -> str:
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **kw)
    return "".join([c async for c in gen])


def _tool_msgs(model: _Model, round_index: int = 1) -> list:
    return [m for m in model.calls[round_index]["messages"] if m.role == "tool"]


# =========================================================================== #
# C4 — the picture itself
# =========================================================================== #


def test_only_an_adapter_that_carries_a_tools_picture_and_sees_counts():
    class _A(LLMAdapter):
        def __init__(self, takes: bool, caps: dict):
            self.takes_tool_pictures, self._caps = takes, caps

        def capabilities(self):
            return self._caps

        async def complete(self, **kw):  # pragma: no cover — never called
            raise AssertionError

    full = {"tool_use": True, "vision": True}
    assert carries_tool_pictures(_A(True, full))
    assert not carries_tool_pictures(_A(False, full)), "an adapter that drops tool pictures"
    assert not carries_tool_pictures(_A(True, {"tool_use": True, "vision": False})), "no vision"
    assert not carries_tool_pictures(_A(True, {"tool_use": False, "vision": True})), "codex exec fallback"
    assert not carries_tool_pictures(
        _A(True, {"tool_use": True, "vision": True, "tool_use_mode": "prompted"})), "prompted wrap flattens"
    from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
    from iron_jarvis.providers.adapters.codex_native.adapter import CodexNativeAdapter
    from iron_jarvis.providers.adapters.subprocess_cli import ClaudeCliAdapter

    assert AnthropicAdapter.takes_tool_pictures and ClaudeCliAdapter.takes_tool_pictures
    assert CodexNativeAdapter.takes_tool_pictures
    assert not LLMAdapter.takes_tool_pictures, "every other adapter drops a tool's picture"


async def test_a_claude_read_of_a_picture_hands_the_model_the_picture_itself(lane):
    """Through the real stream lane: Claude Code's ``Read`` of a .png comes
    back as the PICTURE inside the tool result — no vision model is asked —
    and the turn stays on the model the user picked."""
    platform, ws, wire = lane
    (ws / "shot.png").write_bytes(PNG_1X1)
    model = wire([[ToolCall(id="r1", name="Read", arguments={"file_path": "shot.png"})]])
    text = await _stream(platform, _body(ws, "look at shot.png"), harness="auto")
    assert "event: error" not in text, text[-600:]
    tool = _tool_msgs(model)
    assert tool and tool[-1].images, "the picture rides on the tool result"
    assert base64.b64decode(tool[-1].images[0]["data_b64"]) == PNG_1X1
    assert tool[-1].images[0]["media_type"] == "image/png"
    assert "is attached to this result" in tool[-1].content
    assert model.side_calls == [], "no other model was asked to describe it"
    # the second round went to the same pick (the lane never moves a turn
    # for a tool's picture; the router's own pin is test_harness_pictures_v1371)
    assert model.calls[1]["kw"].get("provider") == CLAUDE[0]


async def test_the_post_lane_hands_the_picture_back_too(lane):
    """MIRROR NOTE (lock-step): the POST lane passes the round's model too."""
    platform, ws, wire = lane
    (ws / "shot.png").write_bytes(PNG_1X1)
    model = wire([[ToolCall(id="r1", name="Read", arguments={"file_path": "shot.png"})]])
    await run_chat_turn(platform, BUILTIN_PERSONAS, _body(ws, "look at shot.png", mode="yolo"), harness="auto")
    tool = _tool_msgs(model)
    assert tool and tool[-1].images and model.side_calls == []


async def test_a_model_that_cannot_be_shown_pictures_gets_a_description_that_says_so(lane):
    """The Claude dialect on a local model behind an OpenAI-compatible server
    (no picture inside a tool result): described as before, and the result
    says the model cannot see it — never a claim that a picture is attached."""
    platform, ws, wire = lane
    (ws / "shot.png").write_bytes(PNG_1X1)
    model = wire([[ToolCall(id="r1", name="Read", arguments={"file_path": "shot.png"})]], who=LOCAL)
    text = await _stream(platform, _body(ws, "look at shot.png", who=LOCAL), harness="claude")
    assert "event: error" not in text, text[-600:]
    tool = _tool_msgs(model)
    assert tool and not tool[-1].images
    assert I.PICTURE_DESCRIBED_NOTE.format(path="shot.png") in tool[-1].content
    assert "A red square on white." in tool[-1].content
    assert "is attached" not in tool[-1].content
    assert len(model.side_calls) == 1, "one model that can see described it"


async def test_codex_view_image_is_an_image_read_too(lane):
    platform, ws, _wire = lane
    turn = M.build(registry=platform.registry, permissions=platform.permissions, requested="auto",
                   provider="openai", model="gpt-5.1", stream=True, today="2026-11-02")
    calls = turn.translate_calls([ToolCall(id="v1", name="view_image", arguments={"path": "a.png"})],
                                 serving=("codex-cli", "gpt-5.1"))
    assert calls[0].name == "view_image"
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="x", config=platform.config,
                      event_bus=platform.event_bus, engine=platform.engine)
    assert turn.ctx_for(ctx, calls[0]).picture_read == ("codex-cli", "gpt-5.1")
    # A call of a tool that is no image read carries nothing.
    other = turn.translate_calls([ToolCall(id="g1", name="glob", arguments={"pattern": "*"})],
                                 serving=("codex-cli", "gpt-5.1"))
    assert turn.ctx_for(ctx, other[0]).picture_read is None


async def test_a_big_picture_is_shrunk_and_one_too_big_even_shrunk_is_described(lane, monkeypatch):
    platform, ws, wire = lane
    big = _noise_png(220)
    (ws / "big.png").write_bytes(big)
    # Bounds small enough for a small test picture (read at call time).
    monkeypatch.setattr(PS, "MAX_IMAGE_BYTES", 40_000)
    model = wire([[ToolCall(id="r1", name="Read", arguments={"file_path": "big.png"})]])
    await _stream(platform, _body(ws, "look at big.png"), harness="auto")
    tool = _tool_msgs(model)[-1]
    assert tool.images and tool.images[0]["media_type"] == "image/jpeg"
    sent = base64.b64decode(tool.images[0]["data_b64"])
    assert len(sent) <= 40_000 < len(big)
    assert "shrunk from" in tool.content
    # Past HARD even shrunk: not attached, described, and said so.
    monkeypatch.setattr(PS, "HARD_IMAGE_BYTES", 10)
    model2 = wire([[ToolCall(id="r2", name="Read", arguments={"file_path": "big.png"})]])
    await _stream(platform, _body(ws, "look again"), harness="auto")
    tool2 = _tool_msgs(model2)[-1]
    assert not tool2.images
    assert I.PICTURE_TOO_LARGE_NOTE.format(path="big.png") in tool2.content


def _picture_read(tmp_path: Path, name: str, data: bytes) -> ToolResult:
    """One image read straight through ``view_image``, served by an adapter
    that takes a tool's picture."""
    import asyncio

    from iron_jarvis.tools.base import ToolContext

    (tmp_path / name).write_bytes(data)

    class _Sees(LLMAdapter):
        takes_tool_pictures = True

        async def complete(self, **kw):  # pragma: no cover
            raise AssertionError

    class _Router:
        class manager:  # noqa: N801 — a stand-in
            @staticmethod
            def get(provider, model=None):
                return _Sees()

    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=None,
                      event_bus=None, engine=None, picture_read=("anthropic", "m"))
    return asyncio.run(I.ViewImageTool(lambda: _Router()).execute({"path": name}, ctx))


def test_a_picture_goes_with_the_media_type_its_bytes_really_are(tmp_path):
    """A JPEG saved as .png: a provider that checks refuses a mismatch, and one
    refused picture fails the whole turn."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 3), (255, 0, 0)).save(buf, format="JPEG")
    res = _picture_read(tmp_path, "really.png", buf.getvalue())
    assert res.ok and res.images and res.images[0]["media_type"] == "image/jpeg"
    assert "4×3" in res.output


def test_a_file_that_is_no_picture_is_refused_never_attached(tmp_path):
    res = _picture_read(tmp_path, "broken.png", b"not an image at all")
    assert not res.ok and not res.images
    assert res.error == I.PICTURE_UNREADABLE.format(path="broken.png")


def test_a_view_image_outside_a_harness_read_is_exactly_as_before(tmp_path):
    """No ``picture_read`` on the context (plain chat, agents, the generic
    dialect's own view_image): the vision model answers, no picture, no note."""
    import asyncio

    (tmp_path / "a.png").write_bytes(PNG_1X1)
    seen: list = []

    class _Router:
        manager = None

        async def complete(self, **kw):
            seen.append(kw)
            return RouteResult(LLMResponse(text="A dot."), "anthropic", "m")

    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r",
                      config=type("C", (), {"roles": {}})(), event_bus=None, engine=None)
    res = asyncio.run(I.ViewImageTool(lambda: _Router()).execute({"path": "a.png"}, ctx))
    assert res.ok and res.output == "A dot." and not res.images and len(seen) == 1


def test_the_dropped_picture_note_speaks_of_a_read_too():
    assert "read the image again" in chat_turn.DROPPED_PICTURE_NOTE


# =========================================================================== #
# C5 — the words tell the truth
# =========================================================================== #


def _turn(platform, *, who=CLAUDE, requested="auto", mode="approve_for_me", **kw) -> M.HarnessTurn:
    return M.build(registry=platform.registry, permissions=platform.permissions, requested=requested,
                   provider=who[0], model=who[1], stream=True, approval_mode=mode, today="2026-11-02", **kw)


def test_the_approval_sentence_is_true_for_each_posture(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ask_first = _turn(platform).rendered
    assert "a command that only reads (ls, git status, cat, grep ...)" in ask_first
    assert "a call of a tool the user already allowed for this session may run without asking" in ask_first
    assert "a look at the page the user's Browser pane shows" in ask_first
    assert M._GUARDED_EDITS_WORDS in ask_first, "an edit of git's own files cards every time"
    low = _turn(platform, low_trust=True).rendered
    assert "a command that only reads" not in low, "low trust keeps every command's card"
    strict = _turn(platform, mode="always_ask").rendered
    assert "a command that only reads" not in strict, "never under Ask before every action"
    line = next(x for x in strict.splitlines() if x.startswith("Calls to "))
    assert "Write" in line and "Edit" in line and "WebFetch" in line, "it cards edits and the web too"
    auto = _turn(platform, mode="auto").rendered
    assert "a quick check find the call routine" in auto
    yolo = _turn(platform, mode="yolo").rendered
    assert "run without pausing, except a few calls that still wait" in yolo
    assert "a git commit or push" in yolo
    assert M._GUARDED_EDITS_WORDS not in yolo, "Don't ask drops the guarded-edit card"
    # A turn whose only tools are allow-tier (no command, no page) is still
    # told about the guarded edits — except under "Don't ask".
    editors = frozenset({"read_file", "edit_file", "write_file"})
    assert M._GUARDED_EDITS_WORDS in _turn(platform, tool_ceiling=editors).rendered
    assert M._GUARDED_EDITS_WORDS not in _turn(platform, mode="yolo", tool_ceiling=editors).rendered
    assert "pause while" not in _turn(platform, tool_ceiling=editors).rendered, "nothing there asks"
    from iron_jarvis.providers import egress

    with egress.local_only_scope(True):
        local = _turn(platform, local_only=True).rendered
    assert "so each of these asks every time" in local and "a command that only reads" not in local


def test_bash_and_qwens_command_tool_wait_two_minutes_when_given_no_timeout():
    assert D.translate(D.CLAUDE, "Bash", {"command": "pytest"}, frozenset({"shell"})).args["timeout_s"] == 120
    assert D.translate(D.CLAUDE, "Bash", {"command": "x", "timeout": 300000},
                       frozenset({"shell"})).args["timeout_s"] == 300
    assert D.translate(D.QWEN, "run_shell_command", {"command": "x"}, frozenset({"shell"})).args["timeout_s"] == 120
    for tool in (D.CLAUDE.lookup("Bash"), D.QWEN.lookup("run_shell_command")):
        assert "120000 (2 minutes) when you give none" in tool.description
        assert "it is not moved to the background" in tool.description


async def test_a_bash_call_with_no_timeout_reaches_the_shell_with_two_minutes(lane, monkeypatch):
    platform, ws, wire = lane
    seen: list[dict] = []

    async def fake(self, args, ctx):
        seen.append(dict(args))
        return ToolResult(ok=True, output="ok")

    monkeypatch.setattr(SandboxedShellTool, "execute", fake)
    wire([[ToolCall(id="b1", name="Bash", arguments={"command": "python -m pytest -q"})]])
    await _stream(platform, _body(ws, "run the tests", mode="yolo"), harness="auto")
    assert seen and seen[0]["timeout_s"] == 120


async def test_a_long_read_stops_at_a_whole_line_inside_one_result_and_says_which(lane):
    """Claude Code's numbered Read of a 3,000-line file: before, the window
    stopped at 100,000 characters and the lane cut ITS middle out, so the
    window's note claimed lines the model never saw. Now the window stops
    inside the turn's limit and says exactly which lines came back."""
    platform, ws, wire = lane
    (ws / "big.txt").write_text("".join(f"line {i:05d} " + "x" * 90 + "\n" for i in range(1, 3001)),
                                encoding="utf-8")
    model = wire([[ToolCall(id="r1", name="Read", arguments={"file_path": "big.txt"})]])
    await _stream(platform, _body(ws, "read big.txt"), harness="auto")
    content = _tool_msgs(model)[-1].content
    assert "characters cut here" not in content, "the lane never cut the window again"
    note = content.rsplit("\n", 1)[-1]
    assert note.startswith("[showing lines 1–") and "of 3,000 — cut at" in note, note
    last = int(note.split("–", 1)[1].split(" ", 1)[0].replace(",", ""))
    assert f"line {last:05d}" in content and f"line {last + 1:05d}" not in content
    assert len(content) <= 32_000


def test_read_file_holds_a_window_to_max_chars_counting_the_line_numbers(tmp_path):
    import asyncio

    from iron_jarvis.tools.base import ToolContext

    (tmp_path / "f.txt").write_text("".join("y" * 50 + "\n" for _ in range(500)), encoding="utf-8")
    ctx = ToolContext(workspace=tmp_path, session_id="s", agent_run_id="r", config=None,
                      event_bus=None, engine=None)
    res = asyncio.run(B.ReadFileTool().execute({"path": "f.txt", "numbered": True, "max_chars": 5_000}, ctx))
    assert res.ok and len(res.output) <= 5_000
    assert "— cut at 5,000 characters; use offset to read more]" in res.output
    # No max_chars: the old bound and the old words.
    whole = asyncio.run(B.ReadFileTool().execute({"path": "f.txt", "numbered": True}, ctx))
    assert "cut at" not in whole.output


def test_a_pass_through_description_never_points_at_a_tool_the_turn_cannot_reach(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    turn = _turn(platform, requested="generic")
    specs = {s["name"]: s["description"] for s in turn.specs(platform.registry)}
    assert "read_document" not in specs["read_file"]
    assert "image_resize" not in specs["view_image"]
    assert "rename_file" not in specs["shell"] and "Prefer the built-in tools for file work." in specs["shell"]
    assert "message_agent" not in specs["delegate"]
    assert "returns a summarized result plus its agent_run_id." in specs["delegate"], "only the clause went"
    assert "create_agent" in specs["spawn_agent"], "named as where an agent came from, never cut"
    # the registered words are untouched (every other lane shows them)
    assert "read_document" in platform.registry.get("read_file").description


def test_the_dialect_advice_names_only_tools_the_turn_shows(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    act = _turn(platform).rendered
    assert "Read a file before you Edit it" in act
    plan = _turn(platform, plan_mode=True).rendered
    assert "Read a file before you Edit it" not in plan, "no Edit in plan mode"
    assert "Use Glob and Grep to find code" in plan
    gpt = _turn(platform, who=("openai", "gpt-5.1")).rendered
    assert "exec_command returns a session id" in gpt and "Edit files with apply_patch." in gpt
    gpt_low = _turn(platform, who=("openai", "gpt-5.1"), low_trust=True).rendered
    assert "exec_command returns a session id" not in gpt_low, "no process_start: its shell returns none"


def test_a_command_tool_says_when_this_reply_cannot_keep_a_program_running(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    full = {s["name"]: s["description"] for s in _turn(platform).specs(platform.registry)}
    assert "run_in_background is not available" not in full["Bash"]
    low = {s["name"]: s["description"] for s in _turn(platform, low_trust=True).specs(platform.registry)}
    assert "run_in_background is not available" in low["Bash"]
    gpt_low = {s["name"]: s["description"]
               for s in _turn(platform, who=("openai", "gpt-5.1"), low_trust=True).specs(platform.registry)}
    assert "no session id is returned" in gpt_low["exec_command"]


def test_process_start_says_a_coding_sessions_program_lives_with_the_session(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    words = platform.registry.get("process_start").description
    assert "until the session or agent run that started it ends" in words
    assert "the end of the agent run that started it" not in words


# =========================================================================== #
# C6 — the note rides the newest reply that ran tools
# =========================================================================== #


def test_the_note_rides_the_newest_reply_that_ran_tools_not_a_later_words_only_one():
    msgs = [
        {"role": "user", "content": "fix cart.py"},
        {"role": "assistant", "content": "Fixed it.",
         "steps": [{"name": "edit_file", "ok": True, "target": "cart.py"}]},
        {"role": "user", "content": "thanks — what did you change?"},
        {"role": "assistant", "content": "The total now rounds.", "steps": []},
        {"role": "user", "content": "now run the tests"},
    ]
    out = S.history_of(msgs, last_work=True)
    assert S.LAST_WORK_LEAD in out[1]["content"] and "edited cart.py" in out[1]["content"]
    assert out[3]["content"] == "The total now rounds.", "the words-only reply is exactly as before"
    assert sum(S.LAST_WORK_LEAD in m["content"] for m in out) == 1


def test_no_reply_that_ran_tools_means_no_note():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hello.", "steps": [{"name": "plan_update", "ok": True}]},
        {"role": "user", "content": "and?"},
        {"role": "assistant", "content": "Nothing else."},
    ]
    assert all(S.LAST_WORK_LEAD not in m["content"] for m in S.history_of(msgs, last_work=True))


def test_a_newest_reply_left_out_of_the_history_never_takes_the_note():
    """A reply that failed with nothing written is not shown; its note would
    vanish with it — the newest SHOWN reply that ran tools carries one."""
    msgs = [
        {"role": "user", "content": "read a.py"},
        {"role": "assistant", "content": "Read it.",
         "steps": [{"name": "read_file", "ok": True, "target": "a.py"}]},
        {"role": "user", "content": "now b.py"},
        {"role": "assistant", "content": "", "steps": [{"name": "read_file", "ok": True, "target": "b.py"}]},
    ]
    out = S.history_of(msgs, last_work=True)
    assert len(out) == 3
    assert "read a.py" in out[1]["content"]


def test_the_picture_note_words_are_one_line_each():
    for words in (I.PICTURE_DESCRIBED_NOTE, I.PICTURE_TOO_LARGE_NOTE):
        assert "{path}" in words and "\n" not in words
    assert json.dumps(D.IMAGE_READ_WORDS)  # plain text
