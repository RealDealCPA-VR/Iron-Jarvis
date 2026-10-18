"""v1.347.0 — a picture made in a NEW chat comes home to that chat.

THE USER'S REPORT (2026-10-17, v1.346.0): "make me an image of an apple" in a
new chat. The ledger, read off the live install: the first call picked the
cheapest Pixio "image" model — an EDITOR that needs an input picture
("422: Field required (body.image_url)"); the model's second call named
"gpt-image-1" and Pixio answered 404 ("Public API model IDs must start with
pixio/"); the third started, the inline wait (45 s) ran out, the job went to
the background — and finished 21 s later with ``thread_id = ""``. A new
chat's first question is sent BEFORE the page's first save names the chat,
so the tool saw no thread id, the job appended nothing, and the bell's row
opened a blank chat page. The picture was only in the Creative library.

Pins, each mutation-checked:

* a job finds its chat through the TURN that asked: the turn's record, the
  live turn's link, or a link waiting for the turn — when it finishes;
* a job that finished BEFORE the link is delivered by
  ``POST /chat/turns/{id}/thread`` (``MediaJobs.home_turn``), exactly once;
* the boot catch-up (``home_strays``) delivers a finished job an older build
  left with no chat, found through the ONE turn record that names it;
* ``chat_append`` skips a second line for the same ``mediaJob``;
* the tool hands the job its ``ctx.turn_id``;
* Pixio's default model works from a PROMPT ALONE (never an editor, a
  reference model or a LoRA, even when cheaper), a bare id gets ``pixio/``,
  and a model that cannot work is refused naming ones that would;
* end to end through the real routes: the user's exact sequence — stream
  with a turn id and no thread id, save the new chat, link the turn — ends
  with the picture IN the chat;
* the bell opens the Creative library for a finished result with no chat.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.ids import utcnow
from iron_jarvis.core.models import ChatThreadRecord, ChatTurnRecord
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon import routes as _routes_pkg  # noqa: F401 — import order
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_append import append_to_chat_thread
from iron_jarvis.daemon.durable_turns import TurnLogs
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.media.base import MediaError
from iron_jarvis.media.jobs import MEDIA_COMPLETED, MediaJobs, media_markdown, ready_line
from iron_jarvis.media.models import FAILED, SUCCEEDED, MediaJobRecord
from iron_jarvis.media.pixio import PixioProvider, model_kind, prompt_only
from iron_jarvis.media.registry import MediaRegistry
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_media_jobs_v1346 import (
    FakeTime,
    SpyFake,
    _events,
    _harness,
    _messages,
    _row,
    _spec,
    _started,
    _thread,
)
from tests.test_media_providers_v1346 import FakeResponse, _pixio

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path))


@pytest.fixture(autouse=True)
def _clean_turns():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _turn_record(engine, turn_id: str, thread_id: str, *, frames: list | None = None, started=None) -> None:
    with session_scope(engine) as db:
        db.add(
            ChatTurnRecord(
                id=turn_id,
                thread_id=thread_id,
                status="done",
                frames_json=json.dumps(frames or []),
                **({"started_at": started} if started is not None else {}),
            )
        )
        db.commit()


def _media_lines(engine, thread_id: str) -> list[dict]:
    return [m for m in _messages(engine, thread_id) if m.get("panelKind") == "media"]


# --------------------------------------------------------------------------- #
# 1. a job finds its chat through the turn
# --------------------------------------------------------------------------- #


async def test_a_new_chats_job_finds_its_chat_through_the_turn_record(app):
    platform, provider, jobs, _ = _harness(app, delay_s=3.0)
    engine = platform.engine
    tid = _thread(engine, [{"role": "user", "content": "make me an image of an apple"}])
    _turn_record(engine, "turn-a", tid)  # the page linked the turn after its first save

    handle = await _started(provider, kind="image")
    job_id = jobs.start_background(
        _spec(provider, handle, kind="image"), thread_id="", session_id="chat", turn_id="turn-a"
    )
    await jobs.drain()

    row = _row(engine, job_id)
    assert row.status == SUCCEEDED
    assert row.thread_id == tid, "the job never learned which chat it belongs to"
    assert row.turn_id == "turn-a"
    assert row.homed_at is not None
    (line,) = _media_lines(engine, tid)
    assert line["content"] == f"Your image is ready.\n\n{media_markdown('image', row.output_path)}"
    assert line["mediaJob"] == job_id  # kept through chat_append's whitelist now
    (done,) = _events(platform, MEDIA_COMPLETED)
    assert done.payload["thread_id"] == tid  # the bell opens THAT chat


async def test_a_job_reads_the_live_turns_link_before_any_record_exists(app):
    platform, provider, jobs, _ = _harness(app, delay_s=3.0)
    engine = platform.engine
    tid = _thread(engine)
    logs = TurnLogs(engine)
    platform.turn_logs = logs
    logs.link_later("turn-b", tid)  # linked before the turn's record was written
    assert logs.thread_for("turn-b") == tid

    handle = await _started(provider, kind="image")
    job_id = jobs.start_background(
        _spec(provider, handle, kind="image"), thread_id="", session_id="chat", turn_id="turn-b"
    )
    await jobs.drain()
    assert _row(engine, job_id).thread_id == tid
    assert len(_media_lines(engine, tid)) == 1


async def test_a_job_that_finished_before_the_link_is_delivered_by_the_link_once(app):
    platform, provider, jobs, _ = _harness(app, delay_s=3.0)
    engine = platform.engine
    handle = await _started(provider, kind="image")
    job_id = jobs.start_background(
        _spec(provider, handle, kind="image"), thread_id="", session_id="chat", turn_id="turn-c"
    )
    await jobs.drain()
    row = _row(engine, job_id)
    assert row.status == SUCCEEDED and row.thread_id == "" and row.homed_at is None
    (done,) = _events(platform, MEDIA_COMPLETED)
    assert done.payload["thread_id"] == ""

    # The page's first save lands and it links the turn.
    tid = _thread(engine, [{"role": "user", "content": "make me an image"}])
    assert await jobs.home_turn("turn-c", tid) == 1
    row = _row(engine, job_id)
    assert row.thread_id == tid and row.homed_at is not None
    (line,) = _media_lines(engine, tid)
    assert line["content"].startswith("Your image is ready.")
    assert line["documents"] == [row.output_path]
    # A second link (a retry, another window) delivers nothing more.
    assert await jobs.home_turn("turn-c", tid) == 0
    assert len(_media_lines(engine, tid)) == 1
    # Another turn's link never takes this job.
    other = _thread(engine)
    assert await jobs.home_turn("turn-zzz", other) == 0
    assert _media_lines(engine, other) == []


async def test_a_failed_job_that_finished_before_the_link_says_so_in_the_chat(app):
    platform, provider, jobs, _ = _harness(app, delay_s=3.0)
    engine = platform.engine

    async def failing(job_id):
        from iron_jarvis.media.base import JobState

        return JobState("failed", error="the service refused the prompt")

    provider.poll = failing  # type: ignore[method-assign]
    handle = await _started(provider, kind="image")
    job_id = jobs.start_background(
        _spec(provider, handle, kind="image"), thread_id="", session_id="chat", turn_id="turn-f"
    )
    await jobs.drain()
    assert _row(engine, job_id).status == FAILED
    tid = _thread(engine)
    assert await jobs.home_turn("turn-f", tid) == 1
    (line,) = _media_lines(engine, tid)
    assert line["content"] == "Your image could not be made: the service refused the prompt"


async def test_a_link_that_lands_while_the_job_runs_is_where_the_result_goes(app):
    platform, provider, jobs, ft = _harness(app, delay_s=30.0)
    engine = platform.engine
    tid = _thread(engine)
    handle = await _started(provider, kind="image")
    job_id = jobs.start_background(
        _spec(provider, handle, kind="image"), thread_id="", session_id="chat", turn_id="turn-r"
    )
    # Let the job write its row and start polling, then link mid-run. Wait
    # for the ROW itself (bounded in real time): 50 bare yields were not
    # enough on a loaded machine (v1.350.0 integrate saw it under -n 4), the
    # link then found no row to home and the next line read thread_id "".
    for _ in range(500):
        if jobs.get_job(job_id) and _row_exists(engine, job_id):
            break
        await asyncio.sleep(0.01)
    assert _row_exists(engine, job_id), "the job never wrote its row"
    assert await jobs.home_turn("turn-r", tid) == 0  # nothing finished yet
    assert jobs.get_job(job_id)["thread_id"] == tid
    await jobs.drain()
    row = _row(engine, job_id)
    assert row.status == SUCCEEDED and row.thread_id == tid and row.homed_at is not None
    assert len(_media_lines(engine, tid)) == 1


def _row_exists(engine, job_id) -> bool:
    with session_scope(engine) as db:
        return db.get(MediaJobRecord, job_id) is not None


# --------------------------------------------------------------------------- #
# 2. the link route and the boot catch-up
# --------------------------------------------------------------------------- #


def _finished_row(engine, tmp_path, job_id: str, *, turn_id: str = "", created=None) -> str:
    out = tmp_path / f"{job_id}.png"
    out.write_bytes(b"\x89PNG fake")
    with session_scope(engine) as db:
        db.add(
            MediaJobRecord(
                id=job_id,
                provider="fake",
                kind="image",
                model="fake/image",
                prompt="an apple",
                status=SUCCEEDED,
                thread_id="",
                turn_id=turn_id,
                session_id="chat",
                provider_job_id="fake-1",
                output_path=str(out),
                **({"created_at": created} if created is not None else {}),
            )
        )
        db.commit()
    return str(out)


def test_the_link_route_delivers_a_result_that_finished_before_it(app, tmp_path):
    from fastapi.testclient import TestClient

    platform = app.state.platform
    platform.media_jobs = MediaJobs(platform)
    engine = platform.engine
    path = _finished_row(engine, tmp_path, "mj_route", turn_id="turn-route")
    tid = _thread(engine, [{"role": "user", "content": "make me an image of an apple"}])
    with TestClient(app) as client:
        res = client.post("/chat/turns/turn-route/thread", json={"thread_id": tid})
        assert res.status_code == 200, res.text
        assert res.json()["media_homed"] == 1
        again = client.post("/chat/turns/turn-route/thread", json={"thread_id": tid})
        assert again.json()["media_homed"] == 0
    (line,) = _media_lines(engine, tid)
    assert media_markdown("image", path) in line["content"]


async def test_the_boot_catch_up_delivers_a_job_an_older_build_left_with_no_chat(app, tmp_path):
    platform = app.state.platform
    jobs = MediaJobs(platform)
    engine = platform.engine
    now = utcnow().replace(tzinfo=None)
    # The v1.346.0 shape: no turn id on the row; the turn's record names the
    # job in the tool's own output, and that turn was linked to the chat.
    _finished_row(engine, tmp_path, "mj_old", created=now)
    tid = _thread(engine)
    frames = [["tool_call", json.dumps({"output": "image started on Pixio (free); ... (job mj_old)"})]]
    _turn_record(engine, "turn-old", tid, frames=frames, started=now - timedelta(minutes=1))
    # Controls: a job named by TWO chats' turns (ambiguous), one whose turn
    # is far older, one already delivered, one with a chat — all untouched.
    _finished_row(engine, tmp_path, "mj_twice", created=now)
    t1, t2 = _thread(engine), _thread(engine)
    _turn_record(engine, "turn-x1", t1, frames=[["tool_call", '"(job mj_twice)"']], started=now)
    _turn_record(engine, "turn-x2", t2, frames=[["tool_call", '"(job mj_twice)"']], started=now)
    _finished_row(engine, tmp_path, "mj_far", created=now)
    t3 = _thread(engine)
    _turn_record(engine, "turn-far", t3, frames=[["tool_call", '"(job mj_far)"']], started=now - timedelta(hours=5))
    _finished_row(engine, tmp_path, "mj_done", created=now)
    with session_scope(engine) as db:
        r = db.get(MediaJobRecord, "mj_done")
        r.homed_at = now
        db.add(r)
        db.commit()
    t4 = _thread(engine)
    _turn_record(engine, "turn-done", t4, frames=[["tool_call", '"(job mj_done)"']], started=now)

    assert await jobs.home_strays() == 1
    (line,) = _media_lines(engine, tid)
    assert line["mediaJob"] == "mj_old"
    assert _row(engine, "mj_old").thread_id == tid and _row(engine, "mj_old").homed_at is not None
    for t in (t1, t2, t3, t4):
        assert _media_lines(engine, t) == []
    # Idempotent: the next boot finds nothing to do.
    assert await jobs.home_strays() == 0
    assert len(_media_lines(engine, tid)) == 1


def test_a_second_line_for_the_same_media_job_is_skipped(app):
    engine = app.state.platform.engine
    tid = _thread(engine)
    line = ready_line("image", "C:/x/mj_1.png", "mj_1")
    first = append_to_chat_thread(engine, tid, [line])
    second = append_to_chat_thread(engine, tid, [line])
    assert first["appended"] == 1 and second["skipped"] == 1 and second["appended"] == 0
    assert len(_media_lines(engine, tid)) == 1
    # A different job is a different line.
    assert append_to_chat_thread(engine, tid, [ready_line("image", "C:/x/mj_2.png", "mj_2")])["appended"] == 1


# --------------------------------------------------------------------------- #
# 3. the tool hands over the turn
# --------------------------------------------------------------------------- #


async def test_the_tool_hands_the_background_job_its_turn_id(app, tmp_path):
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.media_tool import GenerateMediaTool

    platform = app.state.platform
    ft = FakeTime()
    provider = SpyFake(enabled=True, delay_s=120.0, clock=ft.clock)
    platform.media = MediaRegistry([provider], config=platform.config)
    seen: list[dict] = []

    class _Jobs:
        def start_background(self, spec, **kw):
            seen.append(kw)
            return "mj_spy"

    platform.media_jobs = _Jobs()
    tool = GenerateMediaTool(platform, clock=ft.clock, sleep=ft.sleep)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = ToolContext(
        workspace=ws, session_id="chat", agent_run_id="chat", turn_id="turn-tool",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )
    res = await tool.execute({"kind": "video", "prompt": "waves"}, ctx)
    assert res.ok, res.error
    assert seen and seen[0]["turn_id"] == "turn-tool" and seen[0]["thread_id"] == ""


# --------------------------------------------------------------------------- #
# 4. Pixio: a default that works from a prompt; a named model checked first
# --------------------------------------------------------------------------- #

# Shaped like the real catalog (measured 2026-10-17): the CHEAPEST "image"
# rows are an editor, a reference model and a LoRA — the v1.346.0 default.
_CATALOG = {"models": [
    {"id": "pixio/gpt-image-1/edit", "type": "image-to-image", "inputs": []},
    {"id": "pixio/image-01/subject-reference", "type": "text-to-image", "inputs": [
        {"name": "prompt", "required": True},
        {"name": "image_url", "label": "Subject Reference Image", "type": "file", "required": True},
    ]},
    {"id": "pixio/flux-2/klein/4b/base/lora", "type": "text-to-image", "inputs": []},
    {"id": "pixio/gpt-image-1", "type": "text-to-image", "inputs": []},
    {"id": "pixio/bria/base", "type": "text-to-image", "inputs": [{"name": "prompt", "required": True}]},
    {"id": "pixio/seedance/i2v", "type": "image-to-video", "inputs": [{"name": "image_url", "required": True}]},
    {"id": "pixio/video-extend", "type": "video-extend", "inputs": []},
    {"id": "pixio/seedance/t2v", "type": "text-to-video", "inputs": []},
    {"id": "pixio/lyria3-clip", "name": "Lyria 3 Clip", "type": "text-to-audio", "inputs": []},
    {"id": "pixio/audio/speech", "name": "Text to Speech", "type": "text-to-audio",
     "inputs": [{"name": "text", "required": True}]},
    {"id": "pixio/cartesia/tts", "name": "Cartesia", "type": "text-to-audio", "inputs": [
        {"name": "text", "required": True}, {"name": "voice_id", "label": "Voice", "required": True},
    ]},
    {"id": "pixio/mesh", "type": "text-to-3d", "inputs": []},
]}
_PRICING = {"pricing": [
    {"modelId": "pixio/gpt-image-1/edit", "yourCredits": 0},
    {"modelId": "pixio/image-01/subject-reference", "yourCredits": 0},
    {"modelId": "pixio/flux-2/klein/4b/base/lora", "yourCredits": 0},
    {"modelId": "pixio/gpt-image-1", "yourCredits": 2},
    {"modelId": "pixio/bria/base", "yourCredits": 1},
    {"modelId": "pixio/seedance/i2v", "yourCredits": 0},
    {"modelId": "pixio/video-extend", "yourCredits": 0},
    {"modelId": "pixio/seedance/t2v", "yourCredits": 3},
    {"modelId": "pixio/lyria3-clip", "yourCredits": 6},
    {"modelId": "pixio/audio/speech", "yourCredits": 10},
    {"modelId": "pixio/cartesia/tts", "yourCredits": 1},
]}


def _catalog_pixio():
    return _pixio({
        ("GET", "/api/v1/models"): FakeResponse(200, _CATALOG),
        ("GET", "/api/v1/pricing"): FakeResponse(200, _PRICING),
    })


def test_model_kind_reads_what_a_model_makes_and_prompt_only_what_it_needs():
    rows = {r["id"]: r for r in _CATALOG["models"]}
    assert model_kind(rows["pixio/seedance/i2v"]) == "video"  # image IN, video OUT
    assert model_kind(rows["pixio/gpt-image-1/edit"]) == "image"
    assert model_kind(rows["pixio/lyria3-clip"]) == "music"
    assert model_kind(rows["pixio/audio/speech"]) == "audio"
    assert model_kind(rows["pixio/mesh"]) == ""
    # The v1.346.0 rows without an in/out type still read by their words.
    assert model_kind({"id": "pixio/flux", "type": "image"}) == "image"
    assert model_kind({"id": "pixio/song", "kind": "music"}) == "music"
    working = {mid for mid, r in rows.items() if prompt_only(r)}
    assert working == {
        "pixio/gpt-image-1", "pixio/bria/base", "pixio/seedance/t2v", "pixio/lyria3-clip",
        "pixio/audio/speech", "pixio/mesh",
    }


async def test_the_default_model_works_from_a_prompt_even_when_an_editor_is_cheaper():
    p, _ = _catalog_pixio()
    assert await p.default_model("image") == "pixio/bria/base"  # NOT the 0-credit editor
    assert await p.default_model("video") == "pixio/seedance/t2v"  # NOT image-to-video
    assert await p.default_model("music") == "pixio/lyria3-clip"
    assert await p.default_model("audio") == "pixio/audio/speech"  # cartesia needs a voice


async def test_a_named_model_is_checked_before_any_quote():
    p, http = _catalog_pixio()
    model, params = await p.prepare("gpt-image-1", {"prompt": "an apple"}, "image")
    assert model == "pixio/gpt-image-1" and params == {"prompt": "an apple"}

    with pytest.raises(MediaError) as editor:
        await p.prepare("pixio/gpt-image-1/edit", {"prompt": "an apple"}, "image")
    assert "needs an input image as well as a prompt" in editor.value.words
    assert "try pixio/bria/base or pixio/gpt-image-1" in editor.value.words

    with pytest.raises(MediaError) as ref:
        await p.prepare("pixio/image-01/subject-reference", {"prompt": "x"}, "image")
    assert "needs Subject Reference Image" in ref.value.words

    with pytest.raises(MediaError) as unknown:
        await p.prepare("flux-pro-9", {"prompt": "x"}, "image")
    assert unknown.value.words.startswith("Pixio has no model called pixio/flux-pro-9 — try ")

    with pytest.raises(MediaError) as wrong:
        await p.prepare("pixio/gpt-image-1", {"prompt": "x"}, "video")
    assert "makes image, not video" in wrong.value.words and "pixio/seedance/t2v" in wrong.value.words

    with pytest.raises(MediaError) as voice:
        await p.prepare("pixio/cartesia/tts", {"prompt": "hello"}, "audio")
    assert "needs Voice" in voice.value.words

    # A speech model's "text" is filled from the prompt; music/audio are one family.
    model, params = await p.prepare("pixio/audio/speech", {"prompt": "hello"}, "audio")
    assert params == {"prompt": "hello", "text": "hello"}
    assert (await p.prepare("pixio/lyria3-clip", {"prompt": "a jingle"}, "audio"))[0] == "pixio/lyria3-clip"
    # With a reference file the editor is the right tool.
    assert (await p.prepare("pixio/gpt-image-1/edit", {"prompt": "x"}, "image", has_refs=True))[0] == (
        "pixio/gpt-image-1/edit"
    )
    assert len(http.calls_to("/models")) == 1, "the catalog is read once and cached"


async def test_no_catalog_means_only_the_prefix_is_fixed():
    p, _ = _pixio({("GET", "/api/v1/models"): FakeResponse(503, {"error": "down"})})
    assert await p.prepare("gpt-image-1", {"prompt": "x"}, "image") == ("pixio/gpt-image-1", {"prompt": "x"})


# --------------------------------------------------------------------------- #
# 5. end to end: the user's exact sequence through the real routes
# --------------------------------------------------------------------------- #


async def test_the_users_new_chat_sequence_ends_with_the_picture_in_the_chat(app, tmp_path):
    platform = app.state.platform
    ft = FakeTime()
    # The live picture outlived the 45 s inline wait and went to the
    # background; an inline wait of 0 sends it there at once.
    platform.config.media_inline_wait_s = 0
    provider = SpyFake(enabled=True, delay_s=120.0, clock=ft.clock)
    platform.media = MediaRegistry([provider], config=platform.config)
    jobs = MediaJobs(platform, sleep=ft.sleep, clock=ft.clock)
    platform.media_jobs = jobs
    tool = platform.registry.get("generate_media")
    tool._clock, tool._sleep = ft.clock, ft.sleep
    tool._quotes.clear()

    state = {"n": 0}

    async def fake_stream(*args: Any, **kw: Any):
        n = state["n"]
        state["n"] += 1
        if n == 0:
            # "free" in the prompt: the fake quotes it free (the user's Pixio
            # quote was "free (covered by your plan)"), so no card stands in
            # the way — exactly the live turn.
            call = ToolCall(id="c1", name="generate_media", arguments={"kind": "image", "prompt": "a free apple"})
            yield {"type": "final", "response": LLMResponse(text="", tool_calls=[call], usage={}),
                   "provider": "mock", "model": "mock"}
        else:
            text = "Your apple image is generating now; it will appear in this chat."
            yield {"type": "text", "text": text}
            yield {"type": "final", "response": LLMResponse(text=text, usage={}),
                   "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream
    question = {"role": "user", "content": "make me an image of an apple"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
        # The page's first send in a NEW chat: a turn id, no thread id.
        body = {
            "messages": [question], "turn_id": "turn-apple", "auto_tools": True,
            "approval_mode": "approve_for_me", "tools": [],
        }
        res = await client.post("/chat/stream", json=body, timeout=30)
        assert res.status_code == 200
        assert "(job mj_" in res.text, res.text[-800:]
        # The page's first save names the chat, then it links the turn.
        saved = await client.put("/chat/threads/new", json={"messages": [question], "title": "apple"})
        assert saved.status_code == 200, saved.text
        tid = saved.json()["id"]
        linked = await client.post("/chat/turns/turn-apple/thread", json={"thread_id": tid})
        assert linked.status_code == 200
        await jobs.drain()

    lines = _media_lines(platform.engine, tid)
    assert len(lines) == 1, _messages(platform.engine, tid)
    assert lines[0]["content"].startswith("Your image is ready.\n\n![image](<")
    assert Path(lines[0]["documents"][0]).is_file()
    assert provider.starts == 1  # one generation, never a second spend
    with session_scope(platform.engine) as db:
        (row,) = list(db.exec(__import__("sqlmodel").select(MediaJobRecord)))
        assert row.turn_id == "turn-apple" and row.thread_id == tid and row.homed_at is not None


# --------------------------------------------------------------------------- #
# 6. the bell
# --------------------------------------------------------------------------- #


def test_the_bell_opens_the_creative_library_for_a_result_with_no_chat():
    src = _read("dashboard/components/NotificationBell.tsx")
    assert 'return typeof path === "string" && path.trim() ? "/creative" : "/chat";' in src
    assert "href: mediaHref(p.thread_id, ok ? p.path : undefined)," in src
