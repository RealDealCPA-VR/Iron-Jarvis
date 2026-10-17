"""v1.346.0 ("media in chat"), lane X-tool-lanes — ``generate_media`` and the
spend check in both chat lanes and the agent runtime.

THE USER'S RULE: any service they choose (theirs is Pixio), ASK before every
spend, but a generation the service itself calls FREE (Pixio's Maker
allowance) is not a spend and needs no card; the chat UI stays the same — the
media simply appears in the reply; long work finishes in the background and
comes home to the chat it was asked in.

Driven through the REAL app, the REAL ``registry.invoke`` (the v1.270.1
lesson: a stub of ``invoke`` proves intent, not outcome) and the offline
``FakeProvider`` with an INJECTED clock (no wait, no network, no Pixio/OpenAI
call). The model is the only double; where the contract says so, a tool's
``execute`` is stubbed and nothing else.

Pins:
* the tool: object schema, registered, ASK tier, low-trust denied, carded
  in a local-only turn, never in ``INTERNET_TOOLS``; provider choice;
* the stream lane: a FREE quote runs with NO card and the ledger row says
  free; a PAID quote CARDS with the quote's words (``cost_note``), in yolo
  too, and runs after Allow through the real registry; ``always`` is read as
  once; ``media_ask_when_free`` forces the card; a local-only turn cards
  "Allow once" with the words;
* BOTH lanes: an image under ``media_inline_wait_s`` comes back inline and
  the reply gains the image line even when the model wrote nothing about
  it; a path the model already showed is not doubled;
* a video hands off to ``MediaJobs`` with the chat's thread id, the output
  says it will appear in this chat, and the job really comes home;
* a 402 / 422 / 429 start answers plain words; low trust denies;
* the runtime passes ``thread_id`` and pauses with ``cost_note``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import select

import iron_jarvis.daemon.routes.chat as chat_routes
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, ChatThreadRecord, SessionStatus, ToolInvocation
from iron_jarvis.core.trust import LOW_TRUST_DENY
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import append_inline_media, media_from_result, run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.media.base import MediaError
from iron_jarvis.media.fake import FakeProvider
from iron_jarvis.media.jobs import MediaJobs, media_markdown
from iron_jarvis.media.registry import MediaRegistry
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import egress
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import Reversibility, SpendCheck, ToolContext, ToolResult
from iron_jarvis.tools.media_tool import GenerateMediaTool

TOOL = "generate_media"
FREE_WORDS = "Generate 1 image on Fake media: free (Maker: 4 left today)"
PAID_WORDS = "Generate 1 image on Fake media for 7 credits (fake price)"
MAKE = "make a picture of a cat"  # the sentence that ASK-arms the tool


@pytest.fixture(autouse=True)
def _clean_turns():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class FakeTime:
    """The injected clock + sleep: a sleep ADVANCES the clock, nothing waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


class SpyFake(FakeProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.starts: list[tuple[str, str, str]] = []  # (model, kind, idem_key)
        self.quotes = 0
        self.polls = 0

    async def quote(self, model, params, kind):
        self.quotes += 1
        return await super().quote(model, params, kind)

    async def poll(self, job_id):
        self.polls += 1
        return await super().poll(job_id)

    async def start(self, model, params, kind, idem_key):
        self.starts.append((model, kind, idem_key))
        return await super().start(model, params, kind, idem_key)


def _wire(platform, *, delay_s: float = 3.0, enabled: bool = True):
    """The fake service behind ``platform.media`` + the tool on the fake clock."""
    ft = FakeTime()
    provider = SpyFake(enabled=enabled, delay_s=delay_s, clock=ft.clock)
    platform.media = MediaRegistry([provider], config=platform.config)
    platform.media_jobs = MediaJobs(platform, sleep=ft.sleep, clock=ft.clock)
    tool = platform.registry.get(TOOL)
    assert isinstance(tool, GenerateMediaTool)
    tool._clock = ft.clock
    tool._sleep = ft.sleep
    tool._quotes.clear()
    return provider, tool, ft


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


def _ctx(platform, workspace: Path, **kw) -> ToolContext:
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace, session_id="chat", agent_run_id="chat",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine, **kw,
    )


def _script(app, calls: list[ToolCall], final: str = "Done.") -> None:
    """Round 0 makes *calls*; round 1 answers *final*."""
    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        n = state["n"]
        state["n"] += 1
        if n == 0:
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=calls, usage={}),
                   "provider": "mock", "model": "mock"}
        else:
            yield {"type": "text", "text": final}
            yield {"type": "final", "response": LLMResponse(text=final, usage={}),
                   "provider": "mock", "model": "mock"}

    app.state.platform.router.stream = fake_stream


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


def _stream(app, body: dict, *, answer: str = "once", on_card=None, **kw) -> list[tuple[str, dict]]:
    platform = app.state.platform
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))

    async def go():
        gen = await chat_routes.stream_chat_turn(platform, {}, ChatBody(**body), **kw)
        seen = []
        async for chunk in gen:
            for ev, data in _frames(chunk):
                seen.append((ev, data))
                if ev == "approval":
                    if on_card is not None:
                        on_card(data)
                    approvals.resolve(data["id"], answer)
        return seen

    return asyncio.run(asyncio.wait_for(go(), 30))


def _cards(frames) -> list[dict]:
    return [d for ev, d in frames if ev == "approval"]


def _finished(frames, name=TOOL) -> list[dict]:
    return [d for ev, d in frames if ev == "tool_call" and d.get("status") == "finished" and d.get("name") == name]


def _done(frames) -> dict:
    return [d for ev, d in frames if ev == "done"][-1]


def _body(text: str, *, mode: str = "approve_for_me", tools: list[str] | None = None, **over) -> dict:
    """A stream body with the page's Auto switch on, so the SENTENCE ask-arms
    the tool (visible, never granted) exactly as the dashboard does."""
    return {
        "messages": [{"role": "user", "content": text}],
        "tools": tools or [], "approval_mode": mode, "auto_tools": True, **over,
    }


def _call(prompt: str, kind: str = "image", cid: str = "c1", **extra) -> ToolCall:
    return ToolCall(id=cid, name=TOOL, arguments={"kind": kind, "prompt": prompt, **extra})


def _ledger(platform, name=TOOL) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).where(ToolInvocation.tool == name)))
        for r in rows:
            db.expunge(r)
        return rows


def _made_png(frames) -> str:
    """The absolute path the inline image landed at, read off the done frame."""
    docs = _done(frames)["documents"]
    pngs = [p for p in docs if p.endswith(".png")]
    assert pngs, docs
    return pngs[0]


# --------------------------------------------------------------------------- #
# 1. the tool itself
# --------------------------------------------------------------------------- #


def test_the_tool_is_registered_with_an_object_schema_on_the_ask_tier(app):
    reg = app.state.platform.registry
    tool = reg.get(TOOL)
    assert isinstance(tool, GenerateMediaTool)
    spec = tool.spec()
    assert spec["input_schema"]["type"] == "object"
    assert set(spec["input_schema"]["required"]) == {"kind", "prompt"}
    assert tool.reversibility is Reversibility.IRREVERSIBLE
    assert default_permissions()[TOOL] == "ask"
    assert TOOL in LOW_TRUST_DENY
    # Carded in a local-only turn (not dropped): the one-message OK on the
    # card is the consent for that prompt to leave this PC.
    assert TOOL in egress.ask_first_tools(reg)
    assert TOOL not in egress.INTERNET_TOOLS


async def test_provider_choice_explicit_setting_only_connected_and_the_refusals(app, tmp_path):
    platform = app.state.platform
    provider, tool, _ = _wire(platform)
    ctx = _ctx(platform, tmp_path / "ws")
    # Explicit unknown: a plain refusal naming the known ones.
    bad = await tool.execute({"kind": "image", "prompt": "x", "provider": "midjourney"}, ctx)
    assert not bad.ok and "midjourney" in bad.error and "fake" in bad.error
    assert provider.starts == []
    # The only connected one is chosen with no name.
    ok = await tool.execute({"kind": "image", "prompt": "a free cat"}, ctx)
    assert ok.ok, ok.error
    assert provider.starts[-1][1] == "image"
    # The setting names it too.
    platform.config.media_provider = "fake"
    ok2 = await tool.execute({"kind": "image", "prompt": "a free dog"}, ctx)
    assert ok2.ok, ok2.error
    platform.config.media_provider = "auto"
    # Nothing connected: a refusal that says what to connect, no start.
    _wire(platform, enabled=False)
    tool = platform.registry.get(TOOL)
    none = await tool.execute({"kind": "image", "prompt": "x"}, ctx)
    assert not none.ok and "no media service is connected" in none.error.lower()
    # A bad kind is refused in words.
    kind = await tool.execute({"kind": "hologram", "prompt": "x"}, ctx)
    assert not kind.ok and "hologram" in kind.error


async def test_the_spend_check_quotes_once_and_execute_reuses_it(app, tmp_path):
    platform = app.state.platform
    provider, tool, _ = _wire(platform)
    ctx = _ctx(platform, tmp_path / "ws")
    free = await tool.spend_check({"kind": "image", "prompt": "a free cat"}, ctx)
    assert free == SpendCheck(ask=False, words=FREE_WORDS, cost_note="free (Maker: 4 left today)")
    paid = await tool.spend_check({"kind": "image", "prompt": "a paid cat"}, ctx)
    assert paid == SpendCheck(ask=True, words=PAID_WORDS, cost_note="7 credits (fake price)")
    assert provider.quotes == 2
    res = await tool.execute({"kind": "image", "prompt": "a free cat"}, ctx)
    assert res.ok, res.error
    assert provider.quotes == 2, "execute paid a second estimate"
    assert provider.starts[-1][2].startswith("ij-")
    # Money words ride the output and the data; the file is absolute.
    assert "free (Maker: 4 left today)" in res.output
    path = res.data["media"][0]
    assert Path(path).is_absolute() and Path(path).is_file()
    assert res.created_paths == [path]
    # What was charged (the fake says "fake") beside what was quoted.
    assert res.data["cost_note"] == "fake; quoted free (Maker: 4 left today)"
    # A platform-less tool (no registry) has no opinion and refuses in words.
    bare = GenerateMediaTool(SimpleNamespace())
    assert await bare.spend_check({"kind": "image", "prompt": "x"}, ctx) is None
    assert not (await bare.execute({"kind": "image", "prompt": "x"}, ctx)).ok


# --------------------------------------------------------------------------- #
# 2. the stream lane: free runs, paid cards
# --------------------------------------------------------------------------- #


def test_a_free_quote_runs_with_no_card_through_the_real_registry_and_the_ledger_says_free(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    _script(app, [_call("a free cat")])
    frames = _stream(app, _body(MAKE))
    assert _cards(frames) == [], "a free generation was carded"
    (fin,) = _finished(frames)
    assert fin["ok"] is True, fin["output"]
    assert provider.starts and provider.starts[0][1] == "image"
    rows = _ledger(platform)
    assert len(rows) == 1 and rows[0].ok is True
    assert "free (Maker: 4 left today)" in rows[0].output
    # The picture is in the reply (the model wrote nothing about it) and
    # the file is in the rail.
    png = _made_png(frames)
    assert media_markdown("image", png) in _done(frames)["reply"]


def test_a_paid_quote_cards_with_the_words_and_runs_after_allow_through_the_real_registry(app, monkeypatch):
    platform = app.state.platform
    provider, tool, _ = _wire(platform)
    ran: list[dict] = []
    fake_png = str(Path(platform.config.home) / "made.png")

    async def _execute(args, ctx):  # ONLY execute is stubbed (the v1.270.1 rule)
        ran.append(dict(args))
        return ToolResult(ok=True, output="image made", data={"media": [fake_png], "kind": "image"})

    monkeypatch.setattr(tool, "execute", _execute)
    _script(app, [_call("a paid cat")])
    before: list[int] = []
    frames = _stream(app, _body(MAKE), on_card=lambda d: before.append(len(ran)))
    (card,) = _cards(frames)
    assert card["tool"] == TOOL
    assert card["cost_note"] == PAID_WORDS
    assert card["can_always"] is False
    assert before == [0], "the tool ran before the card was answered"
    assert ran == [{"kind": "image", "prompt": "a paid cat"}]
    assert _finished(frames)[0]["ok"] is True
    assert provider.starts == []  # the stub never reached the service


def test_a_paid_quote_cards_even_in_yolo_and_a_denied_card_never_runs(app, monkeypatch):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    _script(app, [_call("a paid cat")])
    frames = _stream(app, _body(MAKE, mode="yolo"), answer="deny")
    (card,) = _cards(frames)
    assert card["cost_note"] == PAID_WORDS
    assert provider.starts == []
    (fin,) = _finished(frames)
    assert fin["ok"] is False
    # Control: a FREE one in yolo runs with no card.
    _script(app, [_call("a free cat")])
    frames = _stream(app, _body(MAKE, mode="yolo"))
    assert _cards(frames) == []
    assert len(provider.starts) == 1


def test_two_priced_calls_in_one_yolo_round_share_one_card_that_names_both_quotes(app):
    """X-review pin: the round grouper (`_would_card`) and the per-call
    branch must AGREE on a priced call in yolo — the mutant that let
    `_would_card` answer "" there left the batch card with no count while the
    second call still ran on the first card's answer. And ONE card covering a
    batch names EVERY distinct quote (an image AND a video), not the first
    call's alone."""
    platform = app.state.platform
    provider, _, _ = _wire(platform, delay_s=120.0)
    _script(app, [_call("a paid cat", cid="c1"), _call("a paid wave", kind="video", cid="c2")])
    frames = _stream(app, _body(MAKE, mode="yolo"))
    (card,) = _cards(frames)
    assert card["count"] == 2 and len(card["examples"]) == 2
    assert card["cost_note"] == (
        "Generate 1 image on Fake media for 7 credits (fake price); "
        "Generate 1 video on Fake media for 7 credits (fake price)"
    )
    assert card["can_always"] is False
    assert [d["decision"] for ev, d in frames if ev == "approval_resolved"] == ["once"]
    assert [f["ok"] for f in _finished(frames)] == [True, True]
    assert sorted(s[1] for s in provider.starts) == ["image", "video"]


def test_always_on_a_priced_card_is_read_as_once_and_mints_no_grant(app, monkeypatch):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    _script(app, [_call("a paid cat")])
    frames = _stream(app, _body(MAKE), answer="always")
    assert len(_cards(frames)) == 1
    resolved = [d for ev, d in frames if ev == "approval_resolved"]
    assert [r["decision"] for r in resolved] == ["once"]
    assert platform.grants.list() == []
    assert len(provider.starts) == 1


def test_media_ask_when_free_forces_the_card_with_the_free_words(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    platform.config.media_ask_when_free = True
    try:
        _script(app, [_call("a free cat")])
        frames = _stream(app, _body(MAKE))
        (card,) = _cards(frames)
        assert card["cost_note"] == FREE_WORDS
        assert _finished(frames)[0]["ok"] is True
        assert len(provider.starts) == 1
    finally:
        platform.config.media_ask_when_free = False


def test_a_local_only_turn_cards_allow_once_with_the_words_even_for_a_free_quote(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    _script(app, [_call("a free cat")])
    frames = _stream(app, _body(MAKE, mode="yolo", local_only=True), answer="conversation")
    (card,) = _cards(frames)
    assert card["once_only"] is True and card["can_always"] is False
    assert card["cost_note"] == FREE_WORDS
    assert [d["decision"] for ev, d in frames if ev == "approval_resolved"] == ["once"]
    assert _finished(frames)[0]["ok"] is True
    assert len(provider.starts) == 1


def test_low_trust_keeps_the_tool_away_in_the_stream_lane(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    _script(app, [_call("a free cat")])
    frames = _stream(app, _body(MAKE), trust="low", trust_reason="started from a channel")
    assert _cards(frames) == []
    # The tool is kept away from every list the model sees, so the call is
    # never run (nothing reached the service, no successful frame, no row).
    assert provider.starts == []
    assert all(f["ok"] is False for f in _finished(frames))
    assert not any(r.ok for r in _ledger(platform))
    done = _done(frames)
    assert done["trust"] == "low" and "kept away" in str(done.get("trust_note") or "")
    # Control: the same turn at full trust runs it.
    _script(app, [_call("a free cat")])
    frames = _stream(app, _body(MAKE))
    assert _finished(frames)[0]["ok"] is True and len(provider.starts) == 1


# --------------------------------------------------------------------------- #
# 3. inline media in BOTH lanes
# --------------------------------------------------------------------------- #


def test_the_stream_reply_gains_the_image_line_once_even_when_the_model_shows_it(app):
    platform = app.state.platform
    _wire(platform)
    # The model wrote nothing about the file: the line is appended.
    _script(app, [_call("a free cat")], final="Here you go.")
    frames = _stream(app, _body(MAKE))
    png = _made_png(frames)
    reply = _done(frames)["reply"]
    line = media_markdown("image", png)
    assert reply.startswith("Here you go.")
    assert reply.count(line) == 1
    assert Path(png).is_file()
    # The model already showed a file: that path is not doubled, a new one
    # still gets its line (the model's text names the FIRST file).
    _script(app, [_call("a free cat", cid="c3")], final=f"Done: {line}")
    frames3 = _stream(app, _body(MAKE))
    png3 = _made_png(frames3)
    reply3 = _done(frames3)["reply"]
    assert png3 != png
    assert reply3.count(line) == 1
    assert reply3.count(media_markdown("image", png3)) == 1


def test_append_inline_media_never_doubles_a_path_the_reply_shows():
    p = r"C:\Users\x\Iron Jarvis\media\fake\mj_1.png"
    res = ToolResult(ok=True, output="x", data={"media": [p, p, "relative.png"], "kind": "image"})
    media = media_from_result(TOOL, res)
    assert media == [("image", p)]
    line = media_markdown("image", p)
    assert append_inline_media("Done.", media) == f"Done.\n\n{line}"
    assert append_inline_media(f"See {line}", media) == f"See {line}"
    assert append_inline_media("See C:/Users/x/Iron Jarvis/media/fake/mj_1.png", media).count("mj_1.png") == 1
    assert append_inline_media("", media) == line
    # A failed call or one with no media adds nothing.
    assert media_from_result(TOOL, ToolResult(ok=False, error="no", data={"media": [p]})) == []
    assert append_inline_media("Done.", []) == "Done."


class _PostRouter:
    """The POST lane's router double: round 0 calls the tool, round 1 answers."""

    default_provider = "mock"

    def __init__(self, calls, final="Done."):
        self.n = 0
        self.calls = calls
        self.final = final
        self.tools: list[list[str]] = []

    async def complete(self, **kw):
        self.tools.append([t.get("name") for t in kw.get("tools") or []])
        self.n += 1
        if self.n == 1:
            return RouteResult(LLMResponse(text="", tool_calls=self.calls, usage={}), "mock", "mock")
        return RouteResult(LLMResponse(text=self.final, tool_calls=[], usage={}), "mock", "mock")


def test_the_post_lane_reply_gains_the_image_line_and_the_rail_gets_the_file(app):
    platform = app.state.platform
    provider, _, _ = _wire(platform)
    platform.router = _PostRouter([_call("a free cat")], final="Done.")
    body = ChatBody(messages=[{"role": "user", "content": MAKE}], tools=[TOOL], thread_id="chat_post")
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert TOOL in platform.router.tools[0]
    pngs = [p for p in res["documents"] if p.endswith(".png")]
    assert pngs and Path(pngs[0]).is_file()
    assert res["reply"] == f"Done.\n\n{media_markdown('image', pngs[0])}"
    assert len(provider.starts) == 1


# --------------------------------------------------------------------------- #
# 4. a video hands off, with the chat's thread id, and comes home
# --------------------------------------------------------------------------- #


def test_a_video_hands_off_to_media_jobs_with_the_threads_id_and_says_so(app, monkeypatch):
    platform = app.state.platform
    provider, _, ft = _wire(platform, delay_s=120.0)  # past media_inline_wait_s (45)
    handed: list[dict] = []

    def _spy(spec, *, thread_id="", session_id="chat", project_id=None):
        handed.append({"spec": spec, "thread_id": thread_id, "session_id": session_id, "project_id": project_id})
        return "mj_spy"

    monkeypatch.setattr(platform.media_jobs, "start_background", _spy)
    _script(app, [_call("a free wave", kind="video")])
    frames = _stream(app, _body("make a video of waves", thread_id="chat_t1"))
    assert _cards(frames) == []
    (fin,) = _finished(frames)
    assert fin["ok"] is True
    assert "it will appear in this chat" in fin["output"] and "(job mj_spy)" in fin["output"]
    assert "the bell will say so" in fin["output"]
    assert len(handed) == 1
    assert handed[0]["thread_id"] == "chat_t1"
    assert handed[0]["spec"].provider_job_id.startswith("fake-")
    assert provider.starts[0][2].startswith("ij-")  # the Idempotency-Key
    assert handed[0]["spec"].kind == "video"
    assert handed[0]["spec"].cost_note == "free (Maker: 4 left today)"
    # The handoff was IMMEDIATE: a job whose eta is past media_inline_wait_s
    # is never polled inline first (that would be a 45 s wall-clock stall on
    # every video start before the "it will appear in this chat" answer).
    assert ft.now == 0.0, f"the inline poll loop ran for {ft.now}s before the handoff"
    assert provider.polls == 0, "the inline poll ran before the handoff"
    # No image line: nothing was made inline.
    assert "![" not in _done(frames)["reply"]
    assert _done(frames)["documents"] == []


def test_the_post_lane_hands_a_video_off_with_the_chats_thread_id(app, monkeypatch):
    """Contract 4(a), the POST lane's half: ``ToolContext.thread_id`` is set
    from ``body.thread_id`` so a background generation comes home to the
    chat it was asked in (the stream lane's twin is pinned above)."""
    platform = app.state.platform
    provider, _, ft = _wire(platform, delay_s=120.0)
    handed: list[dict] = []

    def _spy(spec, *, thread_id="", session_id="chat", project_id=None):
        handed.append({"spec": spec, "thread_id": thread_id, "session_id": session_id, "project_id": project_id})
        return "mj_spy"

    monkeypatch.setattr(platform.media_jobs, "start_background", _spy)
    platform.router = _PostRouter([_call("a free wave", kind="video")], final="Started.")
    body = ChatBody(messages=[{"role": "user", "content": "make a video of waves"}], tools=[TOOL], thread_id="chat_post")
    res = asyncio.run(run_chat_turn(platform, {}, body))
    assert len(handed) == 1, handed
    assert handed[0]["thread_id"] == "chat_post"
    assert handed[0]["session_id"] == "chat"
    assert handed[0]["spec"].kind == "video"
    assert len(provider.starts) == 1 and provider.starts[0][2].startswith("ij-")
    assert ft.now == 0.0 and provider.polls == 0
    assert res["reply"] == "Started."
    assert res["documents"] == []


async def test_a_handed_off_video_really_comes_home_to_the_chat(app, tmp_path):
    platform = app.state.platform
    provider, tool, ft = _wire(platform, delay_s=120.0)
    rec = ChatThreadRecord(title="t", messages_json=json.dumps([{"role": "user", "content": "make a video"}]))
    with session_scope(platform.engine) as db:
        db.add(rec)
        db.commit()
        db.refresh(rec)
        tid = rec.id
    ctx = _ctx(platform, tmp_path / "ws", thread_id=tid)
    res = await tool.execute({"kind": "video", "prompt": "a free wave"}, ctx)
    assert res.ok and res.data["started"] is True
    job_id = res.data["job_id"]
    assert f"(job {job_id})" in res.output
    await platform.media_jobs.drain()
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        messages = json.loads(row.messages_json)
    line = messages[-1]
    assert line["content"].startswith("Your video is ready.")
    assert line["panelKind"] == "media"
    assert media_markdown("video", line["documents"][0]) in line["content"]
    assert line["documents"] and Path(line["documents"][0]).is_file()
    assert any(e.type == "media.completed" for e in list(platform.event_bus.history))


# --------------------------------------------------------------------------- #
# 5. the service's refusals, in plain words
# --------------------------------------------------------------------------- #


class _Refusing(SpyFake):
    def __init__(self, error: MediaError, *, at: str = "start", **kw):
        super().__init__(**kw)
        self._error = error
        self._at = at

    async def quote(self, model, params, kind):
        if self._at == "quote":
            raise self._error
        return await super().quote(model, params, kind)

    async def start(self, model, params, kind, idem_key):
        if self._at == "start":
            raise self._error
        return await super().start(model, params, kind, idem_key)


@pytest.mark.parametrize(
    "error",
    [
        MediaError("insufficient_credits", "Pixio: not enough credits — 12 needed, 5 available (short by 7)", shortfall=7, status=402),
        MediaError("content_policy", "Pixio refused the prompt (content policy): tone it down", input_hint="tone it down", final=True, status=422),
        MediaError("concurrency_limit", "Pixio is busy with your other generations — try again in 30 s", retry_after_s=30, status=429),
    ],
    ids=["402", "422", "429"],
)
async def test_a_refused_start_answers_the_services_plain_words(app, tmp_path, error):
    platform = app.state.platform
    ft = FakeTime()
    provider = _Refusing(error, enabled=True, clock=ft.clock)
    platform.media = MediaRegistry([provider], config=platform.config)
    tool = platform.registry.get(TOOL)
    tool._quotes.clear()
    res = await tool.execute({"kind": "image", "prompt": "a paid cat"}, _ctx(platform, tmp_path / "ws"))
    assert res.ok is False
    assert res.error == error.words
    assert "Bearer" not in res.error and "sk-" not in res.error


async def test_a_quote_the_service_refuses_still_asks_in_words_and_a_402_quote_refuses_the_start(app, tmp_path):
    platform = app.state.platform
    ft = FakeTime()
    busy = _Refusing(MediaError("provider_error", "Pixio error 500"), at="quote", enabled=True, clock=ft.clock)
    platform.media = MediaRegistry([busy], config=platform.config)
    tool = platform.registry.get(TOOL)
    tool._quotes.clear()
    ctx = _ctx(platform, tmp_path / "ws")
    sc = await tool.spend_check({"kind": "image", "prompt": "x"}, ctx)
    assert sc is not None and sc.ask is True
    assert "price not known until it runs" in sc.words and "Pixio error 500" in sc.words
    broke = _Refusing(MediaError("insufficient_credits", "Pixio: short by 7", shortfall=7, status=402), at="quote", enabled=True, clock=ft.clock)
    platform.media = MediaRegistry([broke], config=platform.config)
    tool._quotes.clear()
    res = await tool.execute({"kind": "image", "prompt": "x"}, ctx)
    assert not res.ok and res.error == "Pixio: short by 7"
    assert broke.starts == []


# --------------------------------------------------------------------------- #
# 6. the agent runtime: thread_id reaches the tool, a priced ask carries cost_note
# --------------------------------------------------------------------------- #


class _Scripted(MockLLMAdapter):
    pass


def _agent_platform(tmp_path, responses):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Scripted(script=list(responses)))
    return p, Orchestrator(p)


def _tool_turn(prompt: str, kind: str = "video") -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id="c1", name=TOOL, arguments={"kind": kind, "prompt": prompt})], finish_reason="tool_use")


DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[TOOL])


async def test_the_runtime_passes_the_chats_thread_id_and_a_free_call_needs_no_pause(tmp_path, monkeypatch):
    p, orch = _agent_platform(tmp_path, [_tool_turn("a free wave"), LLMResponse(text="Started.", finish_reason="stop")])
    provider, _, _ = _wire(p, delay_s=120.0)
    handed: list[dict] = []

    def _spy(spec, *, thread_id="", session_id="chat", project_id=None):
        handed.append({"thread_id": thread_id, "session_id": session_id})
        return "mj_spy"

    monkeypatch.setattr(p.media_jobs, "start_background", _spy)
    asked: list = []
    p.event_bus.add_handler(lambda e: asked.append(e) if e.type == EventType.APPROVAL_REQUESTED else None)
    s = await orch.create_session("make a video", AgentType.BUILDER, chat_thread_id="chat_run", origin="chat")
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    assert len(provider.starts) == 1, "the free call never reached the service"
    assert handed == [{"thread_id": "chat_run", "session_id": s.id}]
    assert asked == [], "a free generation paused the run"
    rows = _ledger(p)
    assert len(rows) == 1 and rows[0].ok is True and "free (Maker" in rows[0].output


async def test_the_runtime_pauses_a_priced_call_with_cost_note_and_runs_after_allow(tmp_path, monkeypatch):
    p, orch = _agent_platform(tmp_path, [_tool_turn("a paid wave"), LLMResponse(text="Started.", finish_reason="stop")])
    provider, _, _ = _wire(p, delay_s=120.0)
    monkeypatch.setattr(p.media_jobs, "start_background", lambda spec, **kw: "mj_spy")
    asked: list = []

    def _answer(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            p.approvals.resolve(e.payload["approval_id"], "once")

    p.event_bus.add_handler(_answer)
    s = await orch.create_session("make a video", AgentType.BUILDER, chat_thread_id="chat_run", origin="chat")
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    assert len(asked) == 1, asked
    assert asked[0]["tool"] == TOOL
    assert asked[0]["cost_note"] == "Generate 1 video on Fake media for 7 credits (fake price)"
    assert asked[0]["can_always"] is False
    assert len(provider.starts) == 1, "the allowed call never reached the service"


async def test_the_runtime_batches_two_priced_calls_into_one_ask_naming_both_quotes(tmp_path, monkeypatch):
    """X-review pin, the runtime's twin of the stream-lane batch card: a step
    with two priced generations pauses ONCE (v1.247.0's shared pause) and
    the ask's words name both quotes; one Allow runs both."""
    two = LLMResponse(
        tool_calls=[
            ToolCall(id="c1", name=TOOL, arguments={"kind": "image", "prompt": "a paid cat"}),
            ToolCall(id="c2", name=TOOL, arguments={"kind": "video", "prompt": "a paid wave"}),
        ],
        finish_reason="tool_use",
    )
    p, orch = _agent_platform(tmp_path, [two, LLMResponse(text="Started.", finish_reason="stop")])
    provider, _, _ = _wire(p, delay_s=120.0)
    monkeypatch.setattr(p.media_jobs, "start_background", lambda spec, **kw: "mj_spy")
    asked: list = []

    def _answer(e):
        if e.type == EventType.APPROVAL_REQUESTED:
            asked.append(dict(e.payload))
            p.approvals.resolve(e.payload["approval_id"], "once")

    p.event_bus.add_handler(_answer)
    s = await orch.create_session("make a picture and a video", AgentType.BUILDER, chat_thread_id="chat_run", origin="chat")
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    assert done.status is SessionStatus.COMPLETED
    assert len(asked) == 1, asked
    assert asked[0]["count"] == 2
    assert asked[0]["cost_note"] == (
        "Generate 1 image on Fake media for 7 credits (fake price); "
        "Generate 1 video on Fake media for 7 credits (fake price)"
    )
    assert sorted(st[1] for st in provider.starts) == ["image", "video"]
