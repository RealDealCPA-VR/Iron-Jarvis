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
