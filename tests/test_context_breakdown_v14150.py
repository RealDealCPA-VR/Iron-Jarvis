"""v1.415.0: the Session tab's Context drop-down — what fills a coding
session's conversation, part by part.

Chat › Code (Iron Jarvis's own harness): the build turn's done frame and the
reply line it saves carry ``context.breakdown`` — counted from the very strings
the turn sent, with the planner's estimator, so the parts add up EXACTLY to the
report's ``tokens`` (the figure the Compact offer keys off); tool descriptions
are listed apart and never folded into the percent; every key the report had
before is unchanged, and a plain chat turn carries no breakdown at all.

Build › Code (the user's real Claude Code): ``claude_breakdown`` puts Claude
Code's own ``get_context_usage`` answer (recorded live from claude 2.1.316 —
``tests/fixtures/claude_context_usage_v14150.json``) in the same shape, and
``seen.claude_context`` reads the LAST main-loop call the way Claude Code
itself does (an advisor or compaction iteration is not the conversation).

Driven through the REAL app factory and routes; only the model is scripted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.context import breakdown as B
from iron_jarvis.context.budget import estimate_tokens
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.hosted import seen as SEEN
from iron_jarvis.hosted import service as HS
from tests._build_app_helpers_v1409 import client, fake_log, stop_all, until, use_fake
from tests._build_app_pi_helpers_v1411 import use_fake_pi
from tests._build_app_helpers_v1409 import frames as app_frames
from tests._build_app_helpers_v1409 import new_session as new_app_session
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    wire,
)

FIX = Path(__file__).parent / "fixtures"
WINDOW = 200_000


@pytest.fixture()
def app(tmp_path):
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.model_context_windows = {f"{CLAUDE[0]}::{CLAUDE[1]}": WINDOW}
    return a


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "AGENTS.md").write_text("# House rules\n" + "Keep functions short and named plainly.\n" * 40,
                                      encoding="utf-8")
    return folder


def _parts(b: dict) -> dict[str, int]:
    return {p["key"]: p["tokens"] for p in b["parts"]}


async def _turn(app, work, text="add a docstring", turn_id="bt-1"):
    sid = new_session(app, work)["id"]
    wire(app, Script(["Done."]))
    status, raw = await open_turn(app, sid, {"text": text, "turn_id": turn_id})
    assert status == 200, raw
    return sid, dict(frames_of(raw))["done"]


# --------------------------------------------------------------------------- #
# Chat › Code: exact, additive
# --------------------------------------------------------------------------- #


async def test_a_coding_turn_breaks_its_context_down_and_the_parts_add_up_exactly(app, work):
    sid, done = await _turn(app, work)
    ctx = done["context"]
    b = ctx["breakdown"]
    used_parts = sum(p["tokens"] for p in b["parts"] if p["kind"] == "used")
    # The parts add up to EXACTLY what the compaction report measured.
    assert used_parts == b["used"] == ctx["tokens"]
    assert b["window"] == ctx["window"] == WINDOW and b["percent"] == ctx["percent"]
    parts = _parts(b)
    # The folder's AGENTS.md is its own part, and named in the list.
    assert parts["rules"] >= estimate_tokens("Keep functions short and named plainly.\n" * 40) - 5
    assert any(lst["key"] == "rules" and lst["items"][0]["name"] == "AGENTS.md" for lst in b["lists"])
    assert parts["messages"] > 0 and parts["system"] > 0
    # Everything adds up to the window: used + kept free + free space.
    assert sum(p["tokens"] for p in b["parts"]) == WINDOW
    # The automatic summary's mark is the setting's (92 % of the window).
    assert b["auto_compact"] == {"enabled": True, "at": int(WINDOW * 0.92),
                                 "left": int(WINDOW * 0.92) - b["used"], "percent": 92}
    assert parts["buffer"] == WINDOW - int(WINDOW * 0.92)
    # Tool descriptions are listed apart — never in the percent.
    assert {e["key"] for e in b["extra"]} >= {"tools"}
    assert all(e["tokens"] > 0 for e in b["extra"])
    # The reply line keeps exactly the frame's breakdown.
    reply = [m for m in messages_of(app, sid) if m.get("turnId") == "bt-1" and m["role"] == "assistant"][-1]
    assert reply["context"]["breakdown"] == B.clean(b)


async def test_the_breakdown_is_additive_every_old_key_is_unchanged(app, work):
    _sid, done = await _turn(app, work)
    ctx = dict(done["context"])
    ctx.pop("breakdown")
    # Exactly the keys the report always had (plan + compaction report).
    assert {"window", "tokens", "percent", "level", "suggest_at", "auto_at", "compacted"} <= set(ctx)
    assert "breakdown" not in build_routes.context_receipt(ctx)


async def test_a_plain_chat_turn_carries_no_breakdown(app):
    import httpx

    wire(app, Script(["Hello.", "Hello again."]))
    body = {"messages": [{"role": "user", "content": "hi"}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c:
        r = await c.post("/chat", json=body)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["context"]["window"] and "breakdown" not in out["context"]
        r = await c.post("/chat/stream", json=body)
        assert r.status_code == 200, r.text
    done = dict(frames_of(r.text))["done"]
    assert done["context"]["window"] and "breakdown" not in done["context"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_both_lanes_break_a_harness_turn_down_lock_step(tmp_path, monkeypatch, lane):
    """MIRROR NOTE: the POST lane (the benchmark's) carries the same breakdown,
    and off (no harness) carries none — in both lanes."""
    from tests.test_harness_mode_lanes_v1366 import _body, _post, _stream
    from iron_jarvis.platform import build_platform
    from tests.test_harness_mode_lanes_v1366 import Model

    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Rules\nBe exact.\n", encoding="utf-8")
    model = Model(*CLAUDE, ["Done."])
    monkeypatch.setattr(platform.router, "stream", model.stream)
    monkeypatch.setattr(platform.router, "complete", model.complete)
    run = _stream if lane == "stream" else _post
    out = await run(platform, _body(ws), harness=True)
    ctx = (out["done"] if lane == "stream" else out)["context"]
    b = ctx["breakdown"]
    assert sum(p["tokens"] for p in b["parts"] if p["kind"] == "used") == ctx["tokens"]
    assert any(p["key"] == "rules" for p in b["parts"]) and b["extra"]
    off = await run(platform, _body(ws))
    assert "breakdown" not in (off["done"] if lane == "stream" else off)["context"]


def test_a_named_part_must_have_been_sent_to_count():
    system = "BASE PROMPT " * 50 + "\n\n# Rules\nbe kind"
    b = B.harness_breakdown(
        system=system, messages=[{"content": "hello"}], window=10_000,
        pieces={"rules": ["\n\n# Rules\nbe kind"], "skills": ["a playbook that was never sent"]},
        auto_at=0.92, enabled=True,
    )
    parts = _parts(b)
    assert "skills" not in parts
    assert parts["rules"] == estimate_tokens("\n\n# Rules\nbe kind")
    assert parts["system"] + parts["rules"] == estimate_tokens(system)
    assert parts["messages"] == estimate_tokens("hello") + 4


def test_compaction_off_keeps_no_room_for_the_summary():
    b = B.harness_breakdown(system="x" * 400, messages=[], window=10_000, pieces={},
                            auto_at=0.92, enabled=False)
    assert "buffer" not in _parts(b)
    assert b["auto_compact"]["enabled"] is False
    assert sum(p["tokens"] for p in b["parts"]) == 10_000


def test_extension_tools_are_grouped_by_extension():
    specs = [
        {"name": "read_file", "description": "read", "input_schema": {}},
        {"name": "mcp__github__create_issue", "description": "x" * 200, "input_schema": {}},
        {"name": "mcp__github__list_prs", "description": "y" * 100, "input_schema": {}},
        {"name": "mcp__notion__search", "description": "z", "input_schema": {}},
    ]
    b = B.harness_breakdown(system="p", messages=[], window=10_000, pieces={}, tool_specs=specs,
                            auto_at=0.92, enabled=True)
    extra = {e["key"]: e["tokens"] for e in b["extra"]}
    assert extra["tools"] > 0 and extra["extension_tools"] > extra["tools"]
    [lst] = [x for x in b["lists"] if x["key"] == "extension_tools"]
    assert lst["count"] == 3
    assert [i["name"] for i in lst["items"]] == ["github", "notion"]
    assert lst["items"][0]["detail"] == "2 tools"


# --------------------------------------------------------------------------- #
# Build › Code: Claude Code's own /context
# --------------------------------------------------------------------------- #


def _recorded() -> dict:
    return json.loads((FIX / "claude_context_usage_v14150.json").read_text(encoding="utf-8"))


def test_claude_codes_own_answer_in_the_shared_shape():
    raw = _recorded()["response"]
    b = B.claude_breakdown(raw)
    assert b["source"] == "tool"
    assert (b["used"], b["window"], b["percent"]) == (raw["totalTokens"], raw["maxTokens"], raw["percentage"])
    labels = {p["key"]: p["label"] for p in b["parts"]}
    assert labels["system_prompt"] == "System prompt"
    assert labels["memory_files"] == "Instruction files"
    assert labels["autocompact_buffer"] == "Kept free for the automatic summary"
    # Deferred tools are listed apart, never counted.
    assert {e["key"] for e in b["extra"]} == {"system_tools_deferred"}
    assert all(p["key"] != "system_tools_deferred" for p in b["parts"])
    assert b["auto_compact"]["at"] == raw["autoCompactThreshold"]
    assert b["auto_compact"]["left"] == raw["autoCompactThreshold"] - raw["totalTokens"]
    [files] = [x for x in b["lists"] if x["key"] == "rules"]
    assert files["items"][0]["name"] == "CLAUDE.md" and files["items"][0]["detail"] == "Project"
    assert B.clean(b) == b


def test_an_unknown_category_keeps_claude_codes_own_name_and_an_empty_answer_is_none():
    b = B.claude_breakdown({"categories": [{"id": "new_thing", "name": "New thing", "tokens": 5, "kind": "used"}],
                            "totalTokens": 5, "maxTokens": 100, "percentage": 5})
    assert b["parts"] == [{"key": "new_thing", "label": "New thing", "tokens": 5, "kind": "used"}]
    assert B.claude_breakdown({"categories": []}) is None
    assert B.claude_breakdown("nope") is None


# --------------------------------------------------------------------------- #
# The 1 % finding: read the LAST main-loop call, as Claude Code does
# --------------------------------------------------------------------------- #


def _result(iterations):
    return {"type": "result", "usage": {"input_tokens": 9, "iterations": iterations},
            "modelUsage": {"claude-opus-5-5": {"contextWindow": 1_000_000}}}


def test_an_advisor_or_compaction_iteration_is_not_the_conversation():
    main = {"type": "message", "input_tokens": 2, "cache_read_input_tokens": 700_000,
            "cache_creation_input_tokens": 3_000, "output_tokens": 500}
    advisor = {"type": "advisor_message", "input_tokens": 9_000, "output_tokens": 400}
    # Claude Code's own rule (its usage reader): the last iteration that is a
    # message — never an advisor's or a compaction's.
    assert SEEN.claude_context(_result([main, advisor])) == (703_502, 1_000_000)
    assert SEEN.claude_context(_result([{"type": "compaction", "input_tokens": 5}, main])) == (703_502, 1_000_000)
    assert SEEN.claude_context(_result([{**main, "type": "fallback_message"}])) == (703_502, 1_000_000)
    # Nothing but side calls: no reading (never a wrong small one).
    assert SEEN.claude_context(_result([advisor])) is None


# --------------------------------------------------------------------------- #
# The dashboard's fixture: the REAL daemon's answers
# --------------------------------------------------------------------------- #

DASH_FIX = Path(__file__).parents[1] / "dashboard" / "__tests__" / "fixtures" / "context-breakdown-v1415-0.json"


def _shape(v):
    """What the dashboard reads, numbers as their kind (a prompt's size moves
    with every wording change and the date; the SHAPE is the contract)."""
    if isinstance(v, dict):
        return {k: _shape(x) for k, x in v.items() if k != "at"}
    if isinstance(v, list):
        return [_shape(x) for x in v]
    if isinstance(v, bool) or v is None or isinstance(v, str):
        return v
    return "<int>"


async def test_the_dashboard_fixture_is_the_real_daemons_answers(tmp_path, monkeypatch):
    # Chat › Code: a real build turn's saved reply line.
    a = create_app(str(tmp_path / "home1"))
    a.state.platform.config.model_context_windows = {f"{CLAUDE[0]}::{CLAUDE[1]}": WINDOW}
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "AGENTS.md").write_text("# House rules\nKeep functions short.\n", encoding="utf-8")
    sid, _done = await _turn(a, folder)
    line = [m for m in messages_of(a, sid) if m["role"] == "assistant"][-1]
    # Build › Code: Claude Code's own /context through the real route.
    work = tmp_path / "work2"
    work.mkdir()
    use_fake(monkeypatch, "claude_edit", tmp_path / "fake.log")
    monkeypatch.setenv("IJ_FAKE_CONTEXT_USAGE", str(FIX / "claude_context_usage_v14150.json"))
    app = create_app(str(tmp_path / "home2"))
    svc = HS.service_for(app.state.platform)
    async with client(app) as c:
        csid = (await new_app_session(c, work))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{csid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[csid].asks), None) if svc.live.get(csid) else None)
        await c.post(f"/build/app/sessions/{csid}/answer", json={"request_id": ask, "decision": "allow"})
        app_frames((await asyncio.wait_for(task, 30)).text)
        claude = (await c.get(f"/build/app/sessions/{csid}/context")).json()
    await stop_all(app)
    got = {
        "recorded": "tests/test_context_breakdown_v14150.py (IJ_REGEN_CONTEXT_BREAKDOWN=1): a Chat › Code reply "
                    "line's context, and GET /build/app/sessions/{id}/context for Claude Code (its recorded "
                    "get_context_usage, claude 2.1.316)",
        "harness_line_context": line["context"],
        "claude_context": claude,
    }
    import os

    if os.environ.get("IJ_REGEN_CONTEXT_BREAKDOWN") == "1":
        DASH_FIX.write_text(json.dumps(got, indent=1) + "\n", encoding="utf-8")
    kept = json.loads(DASH_FIX.read_text(encoding="utf-8"))
    assert _shape(kept["harness_line_context"]) == _shape(got["harness_line_context"])
    assert _shape(kept["claude_context"]) == _shape(got["claude_context"])
    # The recorded numbers still add up the way the daemon promises.
    b = kept["harness_line_context"]["breakdown"]
    assert sum(p["tokens"] for p in b["parts"] if p["kind"] == "used") == kept["harness_line_context"]["tokens"]


# --------------------------------------------------------------------------- #
# Build › Code: GET /build/app/sessions/{id}/context, through the REAL driver
# --------------------------------------------------------------------------- #


def _asked_context(log: Path) -> int:
    return sum(
        1 for row in fake_log(log)
        if (row.get("in") or {}).get("type") == "control_request"
        and ((row["in"].get("request") or {}).get("subtype") == "get_context_usage")
    )


async def test_claude_code_is_asked_its_own_context_only_when_idle(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    log = tmp_path / "fake.log"
    use_fake(monkeypatch, "claude_edit", log)
    monkeypatch.setenv("IJ_FAKE_CONTEXT_USAGE", str(FIX / "claude_context_usage_v14150.json"))
    app = create_app(str(tmp_path / "home"))
    svc = HS.service_for(app.state.platform)
    async with client(app) as c:
        sid = (await new_app_session(c, work))["id"]
        # Before any process: nothing is started to answer.
        body = (await c.get(f"/build/app/sessions/{sid}/context")).json()
        assert body["reading"] is None and not (svc.live.get(sid) and svc.live[sid].running)
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "do it", "turn_id": "t"}))
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if svc.live.get(sid) else None)
        # Mid-reply (a card waiting): Claude Code is NOT asked.
        mid = (await c.get(f"/build/app/sessions/{sid}/context")).json()
        assert mid["waiting"] is True and _asked_context(log) == 0
        await c.post(f"/build/app/sessions/{sid}/answer", json={"request_id": ask, "decision": "allow"})
        app_frames((await asyncio.wait_for(task, 30)).text)
        body = (await c.get(f"/build/app/sessions/{sid}/context")).json()
    await stop_all(app)
    assert _asked_context(log) == 1
    r = body["reading"]
    raw = _recorded()["response"]
    assert r["source"] == "tool" and r["used"] == raw["totalTokens"] and r["window"] == raw["maxTokens"]
    assert {p["label"] for p in r["parts"]} >= {"System prompt", "Built-in tools", "Instruction files", "Free space"}
    assert r["at"].endswith("Z") and body["waiting"] is False and "note" not in body
    # Claude Code's own /compact, sent as a message.
    assert body["compact"] == {"how": "message", "text": "/compact"}


async def test_a_tool_that_says_only_a_total_says_so_and_offers_its_own_compact(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    use_fake_pi(monkeypatch, "pi_read", tmp_path / "fake.log")
    monkeypatch.setenv("IJ_FAKE_PI_STATS", json.dumps(
        {"tokens": {"total": 9}, "contextUsage": {"tokens": 60_000, "contextWindow": 200_000, "percent": 30}}))
    app = create_app(str(tmp_path / "home"))
    async with client(app) as c:
        sid = (await new_app_session(c, work, harness="pi"))["id"]
        task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": "read it", "turn_id": "t"}))
        app_frames((await asyncio.wait_for(task, 30)).text)
        body = (await c.get(f"/build/app/sessions/{sid}/context")).json()
    await stop_all(app)
    r = body["reading"]
    assert (r["used"], r["window"], r["percent"], r["parts"]) == (60_000, 200_000, 30, [])
    assert body["note"] == "Pi says only how full the conversation is, not what fills it."
    # v1.459.0 (moved on purpose): Pi's own /compact is a press too (the
    # Code view runs Pi's RPC compact for the typed command).
    assert body["compact"] == {"how": "message", "text": "/compact"}
