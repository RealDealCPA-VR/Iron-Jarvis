"""v1.384.0 — coming back to a running Build reply is sent ONLY what it missed.

The user: "when I briefly switched away and back to the IJ harness, my work
jumped, there was a lag getting back to where I was". Returning rejoined the
running reply with ``GET /chat/turns/{id}/stream``, which replayed EVERY
frame from the start (their turn: ~840 frames, 1.2 MB). Now a follower that
asks (``ids``) gets each frame tagged with its RESUME POSITION (``id: <i>:<c>``
— stored frame ``i``, text length ``c``), and ``?after=<i>:<c>`` sends only
what came after it (``daemon/durable_turns``).

THE PROPERTY, over a real recorded turn (the real app, the real lane in
harness mode, a scripted model whose rounds THINK, write in small pieces —
so token and thinking frames MERGE on disk — call tools and start a HELPER):
for EVERY cut point N, the frames a page received up to N plus what the
daemon resumes from the position of frame N, merged the way the store merges
them, equal the turn's stored frames — nothing lost, nothing twice. Both for a
page that left mid-reply (cut in the live stream) and one that left while it
was still reading the stored part (cut in a tagged replay), and while the
turn is still RUNNING. A position the daemon does not hold, and a record
replay, are the whole turn (``resumed: false``). With nothing asked, every
answer is byte-for-byte what it was.

The recorded turn is also written as the dashboard's fixture
(``dashboard/__tests__/fixtures/build-resume-v1384.json``, regenerate with
``IJ_REGEN_BUILD_RESUME=1``) — the page's own fold is checked against it in
``dashboard/__tests__/build-resume-v1384.test.ts``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from iron_jarvis.daemon import durable_turns as dt
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_build_sessions_v1367 import asgi_get, open_turn, turn_log, until
from tests.test_harness_helpers_v1372 import CLAUDE, Model, _helper_reads, _is_main, new_session, wire

FIXTURE = Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures" / "build-resume-v1384.json"
MERGE = ("token", "thinking")


def pieces(text: str, n: int = 4) -> list[str]:
    step = max(1, len(text) // n)
    return [text[i:i + step] for i in range(0, len(text), step)]


class Chatty(Model):
    """The helpers' scripted model, with a main turn that THINKS in pieces
    and writes its words in pieces (before a tool call too). ``gate``: the
    main turn's round ``gate_round`` waits for it after its first pieces."""

    def __init__(self, *a, gate: "asyncio.Event | None" = None, gate_round: int = -1, **kw) -> None:
        super().__init__(*a, **kw)
        self.gate = gate
        self.gate_round = gate_round
        self.main_round = 0

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None,
                     task_class=None, **kw):
        main = _is_main(system)
        move = await self._move(provider, model, system, messages, tools)
        resp = self._resp(move)
        rnd = self.main_round
        if main:
            self.main_round += 1
            for p in pieces(f"Thinking about round {rnd} of the work."):
                yield {"type": "thinking", "text": p}
        text = resp.text or (f"Round {rnd}: reading what I need." if main and resp.tool_calls else "")
        if self.gate is not None and main and rnd == self.gate_round:
            parts = pieces(text)
            for p in parts[:2]:
                yield {"type": "text", "text": p}
            await self.gate.wait()
            for p in parts[2:]:
                yield {"type": "text", "text": p}
        else:
            for p in pieces(text):
                yield {"type": "text", "text": p}
        final = LLMResponse(text=text, tool_calls=list(resp.tool_calls),
                            usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": final, "provider": provider or self.provider,
               "model": model or self.model}


def _main_moves() -> list:
    return [
        [
            ToolCall(id="m1", name="Read", arguments={"file_path": "src/config_reader.py"}),
            ToolCall(id="a1", name="Agent", arguments={
                "description": "search tests", "prompt": "Find config reads in tests.",
                "subagent_type": "Explore",
            }),
        ],
        [ToolCall(id="m2", name="Read", arguments={"file_path": "tests/test_x.py"})],
        "All done: src/config_reader.py reads the config, and the tests read nothing at all.",
    ]


def blocks(raw: str) -> list[tuple[str | None, str, str]]:
    """``[(id, event, data)]`` of an SSE text, data kept as sent."""
    out = []
    for block in raw.split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        fid, ev, data = None, "message", []
        for line in block.split("\n"):
            if line.startswith("id:"):
                fid = line[3:].strip()
            elif line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        out.append((fid, ev, "\n".join(data)))
    return out


def body_of(frames):
    """The frames between ``resume`` and ``caught_up`` (and after, live)."""
    return [(i, e, d) for i, e, d in frames if e not in ("resume", "caught_up")]


def merged(frames) -> list[tuple[str, object]]:
    """Fold ``(event, data)`` the way the store does: adjacent token /
    thinking frames are one frame, their texts joined."""
    out: list[list] = []
    for _i, e, d in frames:
        parsed = json.loads(d) if d else {}
        if e in MERGE and out and out[-1][0] == e:
            out[-1][1] = {**out[-1][1], "text": (out[-1][1].get("text") or "") + (parsed.get("text") or "")}
        else:
            out.append([e, parsed])
    return [(e, p) for e, p in out]


def stored(lg) -> list[tuple[str, object]]:
    return [(e, json.loads(d) if d else {}) for e, d in lg.frames]


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "src").mkdir()
    (folder / "src" / "config_reader.py").write_text("def read():\n    return load_config()\n", encoding="utf-8")
    (folder / "tests").mkdir()
    (folder / "tests" / "test_x.py").write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return folder


def _helper(task, results, rec):
    return _helper_reads("tests/test_x.py", "tests read nothing")(task, results, rec)


async def _recorded(app, work, turn_id: str):
    sid = new_session(app, work, approval_mode="yolo")
    wire(app, Chatty(main=_main_moves(), helper=_helper))
    status, raw = await open_turn(app, sid, {"text": "find every config read", "turn_id": turn_id, "ids": True},
                                  timeout=60)
    assert status == 200, raw
    lg = turn_log(app, turn_id)
    assert lg is not None and lg.status == "done"
    return sid, raw, lg


async def _get(app, path: str) -> str:
    status, text = await asgi_get(app, path)
    assert status == 200, text
    return text if isinstance(text, str) else json.dumps(text)


# --------------------------------------------------------------------------- #
# 1. the recorded turn has what the property needs
# --------------------------------------------------------------------------- #


async def test_the_recorded_turn_merges_tokens_and_thinking_and_has_a_helper(app, work):
    _sid, raw, lg = await _recorded(app, work, "rs-shape")
    live = blocks(raw)
    assert live[0][1] == "resume" and json.loads(live[0][2]) == {"resumed": False, "running": True}
    assert any(e == "caught_up" for _i, e, _d in live)
    events = [e for e, _d in lg.frames]
    assert {"thinking", "token", "tool_call", "helper", "round", "done"} <= set(events)
    # The store MERGED: fewer stored token/thinking frames than live ones.
    live_text = [e for _i, e, _d in body_of(live) if e in MERGE]
    stored_text = [e for e in events if e in MERGE]
    assert len(live_text) > len(stored_text) * 2
    # Every live frame carries a position; markers carry none.
    assert all(i is not None for i, e, _d in body_of(live))
    assert merged(body_of(live)) == stored(lg)


# --------------------------------------------------------------------------- #
# 2. THE PROPERTY: cut at every N, resume from N, merge == the stored turn
# --------------------------------------------------------------------------- #


async def test_cut_anywhere_in_the_live_stream_and_resume_loses_and_repeats_nothing(app, work):
    """OLD: there was no resume point — every return was the whole turn."""
    _sid, raw, lg = await _recorded(app, work, "rs-live")
    live = body_of(blocks(raw))
    want = stored(lg)
    resumes: dict[str, list] = {}
    for n in range(len(live) + 1):
        prefix = live[:n]
        if n == 0:
            again = blocks(await _get(app, "/chat/turns/rs-live/stream?ids=1"))
            assert json.loads(again[0][2])["resumed"] is False
        else:
            after = prefix[-1][0]
            again = blocks(await _get(app, f"/chat/turns/rs-live/stream?after={after}"))
            assert again[0][1] == "resume" and json.loads(again[0][2])["resumed"] is True, (n, after)
            assert again[-1][1] == "caught_up"
        rest = body_of(again)
        resumes[str(n)] = [list(f) for f in rest]
        assert merged(prefix + rest) == want, f"cut at live frame {n}"
    if os.environ.get("IJ_REGEN_BUILD_RESUME") == "1":
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps({
            "_doc": "v1.384.0: a REAL recorded Build turn (tests/test_build_resume_v1384.py). "
                    "stored = the turn log's frames; live = what the POST follower received "
                    "(tagged); resumes[N] = the daemon's answer to ?after=<id of live[N-1]> "
                    "(N=0: ?ids=1). Regenerate with IJ_REGEN_BUILD_RESUME=1.",
            "stored": [list(f) for f in lg.frames],
            "live": [list(f) for f in live],
            "resumes": resumes,
        }, indent=0) + "\n", encoding="utf-8", newline="\n")


async def test_cut_anywhere_in_a_tagged_replay_and_resume_loses_and_repeats_nothing(app, work):
    _sid, _raw, lg = await _recorded(app, work, "rs-replay")
    full = body_of(blocks(await _get(app, "/chat/turns/rs-replay/stream?ids=1")))
    want = stored(lg)
    assert merged(full) == want
    # A replay's positions are the stored frames' own: one per frame.
    assert [i for i, _e, _d in full] == [dt.position_of(lg.frames, j) for j in range(len(lg.frames))]
    for n in range(1, len(full) + 1):
        after = full[n - 1][0]
        rest = body_of(blocks(await _get(app, f"/chat/turns/rs-replay/stream?after={after}")))
        assert merged(full[:n] + rest) == want, f"cut at replay frame {n}"


async def test_a_page_that_left_mid_word_while_it_runs_gets_only_the_rest(app, work):
    sid = new_session(app, work, approval_mode="yolo")
    gate = asyncio.Event()
    wire(app, Chatty(main=_main_moves(), helper=_helper, gate=gate, gate_round=2))
    drop = asyncio.Event()
    # The page leaves in the middle of the final answer's words.
    status, first = await open_turn(app, sid, {"text": "go", "turn_id": "rs-run", "ids": True},
                                    drop=drop, stop_after="All done", timeout=60)
    assert status == 200
    lg = turn_log(app, "rs-run")
    await until(lambda: not lg.watched())
    assert lg.status == "running"
    prefix = body_of(blocks(first))
    after = prefix[-1][0]
    assert prefix[-1][1] == "token" and int(after.split(":")[1]) > 0, "cut inside a merged token frame"
    back = asyncio.create_task(_get(app, f"/chat/turns/rs-run/stream?after={after}"))
    await asyncio.sleep(0.2)
    gate.set()
    rest_raw = await back
    rest = blocks(rest_raw)
    assert rest[0][1] == "resume" and json.loads(rest[0][2]) == {"resumed": True, "running": True}
    await until(lambda: lg.status != "running")
    assert merged(prefix + body_of(rest)) == stored(lg)
    # Only what it missed: the resumed answer is far smaller than the turn.
    assert len(rest_raw) < len(dt.tagged_replay(lg.frames)) / 2


# --------------------------------------------------------------------------- #
# 3. what the daemon does not hold is the whole turn; nothing asked = as before
# --------------------------------------------------------------------------- #


def test_positions_the_log_does_not_hold_are_the_whole_turn():
    lg = dt.TurnLog("t")
    lg.add(dt.sse("token", {"text": "abc"}))
    lg.add(dt.sse("round", {"round": 1}))
    lg.add(dt.sse("thinking", {"text": "hm"}))
    assert lg.frames_after("0:1") == [("0:3", "token", json.dumps({"text": "bc"})),
                                       ("1:0", "round", json.dumps({"round": 1})),
                                       ("2:2", "thinking", json.dumps({"text": "hm"}))]
    assert lg.frames_after("0:3")[0][0] == "1:0", "a whole text frame sends no empty remainder"
    assert lg.frames_after("2:2") == []
    for bad in ("", "x", "1", "0:9", "-1:0", "3:0", "1:4", "a:b", "0:-1"):
        assert lg.frames_after(bad) is None, bad
    head = blocks(lg.tagged_head("0:9"))
    assert head[0][1] == "resume" and json.loads(head[0][2])["resumed"] is False
    assert [e for _i, e, _d in body_of(head)] == ["token", "round", "thinking"]


async def test_a_record_replay_is_the_whole_turn_even_with_a_position(app, work):
    await _recorded(app, work, "rs-record")
    logs = app.state.platform.turn_logs
    lg = logs.get("rs-record")
    await lg.persist_now(app.state.platform.engine)
    logs._logs.pop("rs-record")
    again = blocks(await _get(app, "/chat/turns/rs-record/stream?after=3:0"))
    assert again[0][1] == "resume" and json.loads(again[0][2])["resumed"] is False
    assert merged(body_of(again)) == stored(lg)


async def test_nothing_asked_is_byte_for_byte_the_old_answer(app, work):
    _sid, _raw, lg = await _recorded(app, work, "rs-plain")
    old = "".join(dt.sse(e, d) for e, d in lg.frames)
    assert await _get(app, "/chat/turns/rs-plain/stream") == old
    assert "id:" not in old and "event: resume" not in old
    # A POST turn that does not ask carries no positions either.
    sid = new_session(app, work, approval_mode="yolo")
    wire(app, Chatty(main=["Hi."], helper=_helper))
    status, raw = await open_turn(app, sid, {"text": "hi", "turn_id": "rs-plain-2"}, timeout=60)
    assert status == 200 and "\nid:" not in raw and not raw.startswith("id:") and "event: resume" not in raw


def test_the_fixture_is_what_the_daemon_resumes():
    """The dashboard's fixture (a real recorded turn) still matches what this
    daemon answers for every cut — recomputed from its stored frames."""
    fx = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lg = dt.TurnLog("fixture")
    lg.frames = [list(f) for f in fx["stored"]]
    lg.status = "done"
    live = [tuple(f) for f in fx["live"]]
    for n in range(len(live) + 1):
        want = fx["resumes"][str(n)]
        if n == 0:
            got = [[dt.position_of(lg.frames, j), e, d] for j, (e, d) in enumerate(lg.frames)]
        else:
            got = [list(t) for t in lg.frames_after(live[n - 1][0])]
        assert got == want, n
        assert merged(live[:n] + [tuple(f) for f in want]) == [(e, json.loads(d)) for e, d in lg.frames]
