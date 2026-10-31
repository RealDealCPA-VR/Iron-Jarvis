"""v1.415.0 REVIEW: defects found in the Context drop-down, each pinned red
first.

1. Claude Code's ``get_context_usage`` defaults to ``detail: "full"``, which
   (claude 2.1.316's own schema: "'full' counts each category with the
   token-count API; 'summary' answers from the last response's usage and
   local estimates without the per-category token-count calls") sends the
   conversation to Anthropic's token-count API once per category on every
   read, runs through Claude Code's serialised queue, and holds its teardown
   up to 300 s while one is pending. A Session-tab read asks ``summary``
   (measured on this PC, signed out: same shape, 0.5 s).
2. The same section text handed in twice (a brain base that cannot be used
   renders ``block`` == ``without_tools()``) was counted twice: the memory
   part grew by the block and "System prompt" shrank by it.
3. Pi answers ``contextUsage.tokens: null`` right after it compacts. The
   service fell back to the KEPT reading from before the compaction and
   showed it as current ("90 % used" just after Pi made room).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from iron_jarvis.context import breakdown as B
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import harness_context_breakdown
from iron_jarvis.hosted import pi as PI
from iron_jarvis.hosted import service as HS
from tests._build_app_helpers_v1409 import client, fake_log, stop_all, until, use_fake
from tests._build_app_helpers_v1409 import frames as app_frames
from tests._build_app_helpers_v1409 import new_session as new_app_session
from tests.test_context_breakdown_v14150 import FIX


# --------------------------------------------------------------------------- #
# 1. Claude Code is asked for its cheap summary, never the token-count calls
# --------------------------------------------------------------------------- #


def _context_requests(log):
    return [
        row["in"]["request"]
        for row in fake_log(log)
        if (row.get("in") or {}).get("type") == "control_request"
        and ((row["in"].get("request") or {}).get("subtype") == "get_context_usage")
    ]


async def test_claude_code_is_asked_for_its_summary_never_the_token_count_calls(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_edit", log)
    monkeypatch.setenv("IJ_FAKE_CONTEXT_USAGE", str(FIX / "claude_context_usage_v14150.json"))
    app = create_app(str(tmp_path / "home"))
    svc = HS.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_app_session(c, work))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        app_frames((await asyncio.wait_for(task, 30)).text)
        body = (await c.get(f"/build/app/sessions/{sid}/context")).json()
    await stop_all(app)
    asked = _context_requests(log)
    assert len(asked) == 1 and body["reading"] is not None
    assert asked[0].get("detail") == "summary", asked


# --------------------------------------------------------------------------- #
# 2. One string, counted once
# --------------------------------------------------------------------------- #

BASE = "You are the coding assistant. " * 200
BRAIN = "\n\n# Your durable brain: Notes\nThe user keeps their notes there. " + ("note line. " * 300)


def test_the_same_section_handed_in_twice_is_counted_once():
    system = BASE + BRAIN
    b = B.harness_breakdown(
        system=system, messages=[], window=100_000,
        pieces={"memory": [BRAIN, BRAIN]}, auto_at=0.92, enabled=True,
    )
    parts = {p["key"]: p["tokens"] for p in b["parts"]}
    assert parts["memory"] == estimate_tokens(BRAIN)
    assert parts["system"] == estimate_tokens(system) - estimate_tokens(BRAIN)


def test_a_brain_base_that_cannot_be_used_is_counted_once_in_the_lane_helper():
    # brain/turn.py: with the base unusable, ``block`` is rendered with
    # tools=False, writers=False — byte for byte what ``without_tools()`` gives.
    brain = SimpleNamespace(block=BRAIN, without_tools=lambda: BRAIN)
    d = SimpleNamespace(platform=SimpleNamespace(config=SimpleNamespace(context_compaction={})))
    report = {"window": 100_000, "tokens": estimate_tokens(BASE + BRAIN)}
    harness_context_breakdown(
        d, report, system_before=BASE + BRAIN, system=BASE + BRAIN, messages=[],
        harness=SimpleNamespace(project_map=None), brain=brain,
    )
    parts = {p["key"]: p["tokens"] for p in report["breakdown"]["parts"]}
    assert parts["memory"] == estimate_tokens(BRAIN)


# --------------------------------------------------------------------------- #
# 3. Pi's "not known right now" is not answered with an older reading
# --------------------------------------------------------------------------- #


class _FakePi:
    context_while_working = True
    context_breakdown = PI.PiDriver.context_breakdown

    def __init__(self, answers):
        self.answers = list(answers)

    async def request(self, kind, **_):
        assert kind == "get_session_stats"
        return self.answers.pop(0)


async def test_pi_saying_not_known_after_it_compacts_drops_the_older_reading(tmp_path):
    app = create_app(str(tmp_path / "home"))
    svc = HS.service_for(app.state.platform)
    sid = "sess-pi"
    drv = _FakePi([
        {"contextUsage": {"tokens": 180_000, "contextWindow": 200_000, "percent": 90}},
        # Right after a compaction (pi 1.3.0 docs/rpc-commands.md): unknown.
        {"contextUsage": {"tokens": None, "contextWindow": 200_000, "percent": None}},
    ])
    svc.live[sid] = SimpleNamespace(driver=drv, running=True, busy=False)
    row = SimpleNamespace(id=sid, harness="pi")
    try:
        first = await svc.context_view(row, [])
        assert first["reading"]["used"] == 180_000
        second = await svc.context_view(row, [])
    finally:
        svc.live.pop(sid, None)
    assert second["reading"] is None, second
