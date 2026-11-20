"""Leap 06-a: "rehearse it" from chat, in both lanes.

The ``rehearse`` tool is armed from the user's words by the ONE matcher both
lanes read (``settings.records.record_tools_for``): the stream lane through
``settings_ask_tools``, the POST lane (the phone, an API caller) through
``post_record_tools`` (allow and it only reads). Its result carries the report
card through ``config_cards_from_result``, the helper both lanes extend their
``config_cards`` with. A rehearsal cannot start another.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.chat_turn import (
    config_cards_from_result,
    post_record_tools,
    settings_ask_tools,
)
from iron_jarvis.platform import build_platform
from iron_jarvis.settings.records import record_tools_for
from iron_jarvis.tools.base import ToolContext, ToolResult

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.mark.parametrize("text", [
    "rehearse my morning schedule",
    "do a dry run of the intake rule",
    "what would my Friday schedule do?",
    "Can you rehearse the client mail rule please",
    "test the invoice rule without changing anything",
])
def test_the_words_arm_rehearse(text):
    assert "rehearse" in record_tools_for(text)


@pytest.mark.parametrize("text", [
    "my rule of thumb is to test things",
    "what's on my schedule today?",
    "make a schedule for 8am",
])
def test_other_words_do_not(text):
    assert "rehearse" not in record_tools_for(text)


def test_both_lanes_arm_it(tmp_path):
    d = SimpleNamespace(platform=build_platform(str(tmp_path / "home")))
    assert "rehearse" in settings_ask_tools(d, "rehearse my morning schedule", [], [])
    assert "rehearse" in post_record_tools(d, "rehearse my morning schedule", [])


def test_its_result_carries_the_report_card():
    ok = ToolResult(ok=True, output="Rehearsing.", data={"rehearsal": {"id": "reh_1", "kind": "rule", "name": "Mail"}})
    assert config_cards_from_result("rehearse", ok) == [
        {"kind": "rehearsal", "rehearsal_id": "reh_1", "subject_kind": "rule", "name": "Mail"}
    ]
    # Another tool's data never makes the card; a failed call makes none.
    assert config_cards_from_result("schedule_list", ok) == []
    assert config_cards_from_result("rehearse", ToolResult(ok=False, error="x")) == []


async def test_a_rehearsal_cannot_start_another(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    tool = p.registry.get("rehearse")
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r", config=p.config,
        event_bus=p.event_bus, engine=p.engine, rehearsal_id="reh_outer",
    )
    res = await tool.execute({"kind": "rule", "name": "x"}, ctx)
    assert not res.ok and "cannot start another" in (res.error or "")


async def test_an_unknown_name_is_answered_with_the_names_there_are(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    p.reflex.add(name="Client mail", source="webhook", match="cm", action="session")
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="r", config=p.config,
                      event_bus=p.event_bus, engine=p.engine)
    res = await p.registry.get("rehearse").execute({"kind": "rule", "name": "supplier mail"}, ctx)
    assert not res.ok and "Client mail" in (res.error or "")


async def test_on_the_phone_the_reply_waits_for_the_report_and_says_it(tmp_path, monkeypatch):
    """The POST lane (the phone) draws no card and a rehearsal sends no
    notice, so "the report will show on the card" never came: the reply now
    waits for the report and says it in words. The card lane still answers
    at once and draws the card."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
    from iron_jarvis.tools import rehearsal_runs as RR

    monkeypatch.setattr(RR, "PHONE_POLL_S", 0.02)
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "2025_W2_smith.pdf").write_bytes(b"%PDF smith")
    p.scheduler.add_task("Sort the W2s", "0 8 * * *", kind="task",
                         payload={"task": "Rename the W2s.", "workspace_root": str(folder), "notify": False})

    def scripted():
        state = {"n": 0}

        async def fake_stream(**kw):
            state["n"] += 1
            if state["n"] == 1:
                resp = LLMResponse(text="", tool_calls=[ToolCall(
                    id="c0", name="rename_file",
                    arguments={"path": "2025_W2_smith.pdf", "new_path": "Smith - W2 2025.pdf"})])
            else:
                resp = LLMResponse(text="Renamed.")
                yield {"type": "text", "text": "Renamed."}
            yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

        return fake_stream

    def ctx(cannot_ask: bool) -> ToolContext:
        return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                           event_bus=p.event_bus, engine=p.engine, cannot_ask=cannot_ask)

    tool = p.registry.get("rehearse")
    p.router.stream = scripted()
    phone = await tool.execute({"kind": "schedule", "name": "Sort the W2s"}, ctx(True))
    assert phone.ok, phone.error
    assert phone.output.splitlines() == [
        "Rehearsed the schedule “Sort the W2s”. This would rename 1 file. Nothing was changed.",
        "- Would rename 2025_W2_smith.pdf to Smith - W2 2025.pdf",
        "- Would keep a lesson from this run, if it learned one",
    ]
    assert (folder / "2025_W2_smith.pdf").exists()
    # The card lane answers at once (the card reads the report itself).
    p.router.stream = scripted()
    page = await tool.execute({"kind": "schedule", "name": "Sort the W2s"}, ctx(False))
    assert page.ok and "the report will show on the card" in page.output
    assert config_cards_from_result("rehearse", page)[0]["kind"] == "rehearsal"
    # Let the card lane's rehearsal end before the daemon goes (no task left).
    import asyncio
    import time

    rid = page.data["rehearsal"]["id"]
    deadline = time.monotonic() + 60
    while (await asyncio.to_thread(RR.report, p, rid))["status"] == "running" and time.monotonic() < deadline:
        await asyncio.sleep(0.05)


async def test_on_the_phone_a_rehearsal_that_outlasts_the_wait_is_stopped_and_said(tmp_path, monkeypatch):
    """Nobody could read the report of one that outlasts the wait, so it is
    stopped (no run goes on unseen) and the reply says so, honestly."""
    import asyncio

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.tools import rehearsal_runs as RR

    monkeypatch.setattr(RR, "PHONE_POLL_S", 0.02)
    monkeypatch.setattr(RR, "PHONE_WAIT_S", 0.0)
    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    p.scheduler.add_task("Slow one", "0 8 * * *", kind="task",
                         payload={"task": "Think hard.", "workspace_root": str(tmp_path), "notify": False})
    release = asyncio.Event()

    async def slow_stream(**kw):
        await release.wait()
        yield {"type": "final", "response": LLMResponse(text="Done."), "provider": "mock", "model": "mock"}

    p.router.stream = slow_stream
    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine, cannot_ask=True)
    res = await p.registry.get("rehearse").execute({"kind": "schedule", "name": "Slow one"}, ctx)
    release.set()
    assert res.ok, res.error
    assert res.output.startswith("The rehearsal of the schedule “Slow one” took longer than I can wait here")
    assert "Nothing was changed." in res.output
    view = await asyncio.to_thread(RR.report, p, res.data["rehearsal"]["id"])
    assert view["status"] == "stopped"
