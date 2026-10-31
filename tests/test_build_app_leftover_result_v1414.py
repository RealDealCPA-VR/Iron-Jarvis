"""v1.414.0 (a USER BUG): "I just tested the build module … I got 0 in 0 out.
It didn't work at all?"

Read off the live install: the user's message reached a Claude Code process
that still held earlier work (background agents stopped by a usage limit).
Claude Code answered that earlier work first — two ``result`` frames with 0
tokens in and out, before it even said it STARTED on the user's message — and
the reply ended on the first one. The real answer came after the screen said
"done". A reply now ends only on a result that lands after Claude Code said
``command_lifecycle`` ``started`` for the reply's own message (measured on the
real claude CLI: queued → started → … → result).
"""

from __future__ import annotations

import asyncio

from iron_jarvis.daemon.app import create_app
from tests._build_app_helpers_v1409 import frames, stop_all
from tests.test_build_app_steer_v1412 import (  # noqa: F401 (base is a fixture)
    SYS_INIT, base, claude_script, life, result, say, start, types, use, write_scenario,
)


def zero(text: str = "") -> dict:
    return {"out": {"type": "result", "subtype": "success", "result": text, "session_id": "rec-sid",
                    "usage": {"input_tokens": 0, "output_tokens": 0}, "total_cost_usd": 0.82}}


async def test_results_before_claude_code_starts_on_the_message_do_not_end_the_reply(base, monkeypatch):
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, life(1, "queued"), zero(), zero(), life(1, "started"),
        SYS_INIT, say("m1", "Checking where each team stands."), result("All resumed."),
        life(1, "completed"))), base / "fake.log")
    app = create_app(str(base / "home"))
    c, sid, task = await start(app, base, text="Has it all been worked on?")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert types(evs).count("done") == 1, types(evs)
    assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
    assert evs[-1].get("result") == "All resumed.", "the reply ends on the answer to the user's message"
    assert [e["text"] for e in evs if e["type"] == "text_done"] == ["Checking where each team stands."]
    assert [e["output"] for e in evs if e["type"] == "usage"] == [1], "the earlier work's 0-token result is not this reply's"


async def test_a_message_dropped_before_it_started_ends_the_reply(base, monkeypatch):
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, life(1, "queued"), zero(), life(1, "cancelled"))), base / "fake.log")
    app = create_app(str(base / "home"))
    c, sid, task = await start(app, base, text="hello")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert types(evs).count("done") == 1 and evs[-1]["stop"] == "interrupted"


async def test_an_older_claude_code_with_no_lifecycle_still_ends_on_its_result(base, monkeypatch):
    use(monkeypatch, write_scenario(base, claude_script(
        {"await": "user"}, SYS_INIT, say("m1", "hi"), result("hi"))), base / "fake.log")
    app = create_app(str(base / "home"))
    c, sid, task = await start(app, base, text="hello")
    evs = frames((await asyncio.wait_for(task, 30)).text)
    await c.aclose()
    await stop_all(app)
    assert evs[-1]["type"] == "done" and evs[-1].get("result") == "hi"
