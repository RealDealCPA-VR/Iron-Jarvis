"""v1.356.0, wave 6 lane 6C (G79): a mission approval shows its cost note.

The mission screen draws its OWN approval card (``MissionOutput``), fed by the
mission view's ``waiting_on`` — which read the approvals registry's
``pending_for`` and carried only ``{approval_id, tool, reversible}``. So a
priced ask (an image a teammate wants to generate) showed its price on the
chat card (the ``approval.requested`` event's ``cost_note``) and NOT on the
mission card, where the user presses Allow.

Now the runtime files the ask's cost sentence (``SpendCheck.words``) on the
ONE registry entry (``ChatApprovals.request(cost_note=)``), ``pending_for``
hands it on, and both readers of a run's waiting ask carry it: the session
row's ``waiting_on`` (``routes/sessions._waiting_on``, also a project task's
card) and the mission view's coordinator/member ``waiting_on``
(``agents/mission._waiting``) — a SENTENCE, never the call's arguments.

Driven through the REAL runtime pause (a real platform, the real
``generate_media`` tool quoting a fake priced service, the real orchestrator),
inspected WHILE the run is parked, then allowed.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iron_jarvis.agents.mission import mission_view
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.types import AgentDefinition
from iron_jarvis.core.approvals import COST_NOTE_MAX, ChatApprovals
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, SessionStatus
from iron_jarvis.daemon.routes.sessions import _waiting_on
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

from tests.test_generate_media_tool_v1346 import TOOL, _wire

PRICED = "Generate 1 image on Fake media for 7 credits (fake price)"


class _Scripted(MockLLMAdapter):
    pass


def _platform(tmp_path, responses):
    p = build_platform(str(tmp_path / "home"))
    p.providers.register("mock", lambda model=None: _Scripted(script=list(responses)))
    return p, Orchestrator(p)


def _image_turn(prompt: str) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall(id="c1", name=TOOL, arguments={"kind": "image", "prompt": prompt})],
        finish_reason="tool_use",
    )


DEFN = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=[TOOL])


async def _run_parked(tmp_path, monkeypatch, *, origin: str):
    """Run a session whose one step is a PRICED image; while it is parked on
    the ask, read every surface's view of the wait, then allow it."""
    p, orch = _platform(
        tmp_path, [_image_turn("a red apple, secret-prompt-words"), LLMResponse(text="Done.", finish_reason="stop")]
    )
    _wire(p, delay_s=120.0)
    monkeypatch.setattr(p.media_jobs, "start_background", lambda spec, **kw: "mj_spy")
    seen: dict = {}

    def _on_ask(e):
        if e.type != EventType.APPROVAL_REQUESTED:
            return
        sid = e.session_id
        seen["event"] = dict(e.payload)
        seen["pending"] = p.approvals.pending_for(sid)
        seen["row"] = _waiting_on(SimpleNamespace(platform=p), sid)
        seen["mission"] = mission_view(p, sid)
        p.approvals.resolve(e.payload["approval_id"], "once")

    p.event_bus.add_handler(_on_ask)
    s = await orch.create_session("make a picture of an apple", AgentType.BUILDER, origin=origin)
    done = await asyncio.wait_for(orch.run_session(s.id, DEFN), timeout=60)
    return p, s, done, seen


async def test_a_priced_ask_carries_its_cost_sentence_to_the_session_row_and_the_mission_card(tmp_path, monkeypatch):
    p, s, done, seen = await _run_parked(tmp_path, monkeypatch, origin="job:mission")
    assert done.status is SessionStatus.COMPLETED
    # The ask's own event already said it (v1.346.0) …
    assert seen["event"]["cost_note"] == PRICED
    # … and now the registry entry carries the SAME sentence,
    assert seen["pending"][0]["cost_note"] == PRICED
    # the session row's waiting_on (the session page, a project task's card),
    assert seen["row"]["cost_note"] == PRICED
    assert seen["row"]["tool"] == TOOL
    # and the mission view's waiting entry (MissionOutput's own card).
    waiting = seen["mission"]["coordinator"]["waiting_on"]
    assert waiting["cost_note"] == PRICED
    assert waiting["tool"] == TOOL
    # A sentence, never the call's arguments: no prompt words ride the views.
    for view in (seen["row"], waiting):
        assert "secret-prompt-words" not in repr(view)
        assert "args" not in view


async def test_an_unpriced_ask_keeps_its_exact_shape(tmp_path):
    """No price, no key: the registry entry, the session row and the mission
    view are byte-identical to before for every other ask."""
    reg = ChatApprovals()
    aid, _fut = reg.request("write_file", {"path": "a.txt"}, session_id="s1")
    rows = reg.pending_for("s1")
    assert set(rows[0]) == {"approval_id", "tool", "args", "requested_at"}
    p = build_platform(str(tmp_path / "home"))
    p.approvals = reg
    row = _waiting_on(SimpleNamespace(platform=p), "s1")
    assert "cost_note" not in row
    reg.pop(aid)


async def test_a_blank_or_odd_cost_note_is_not_recorded_and_a_long_one_is_cut():
    reg = ChatApprovals()
    a1, _ = reg.request("t", {}, session_id="s", cost_note="   ")
    a2, _ = reg.request("t", {}, session_id="s2", cost_note=12)  # type: ignore[arg-type]
    assert "cost_note" not in reg.pending_for("s")[0]
    assert "cost_note" not in reg.pending_for("s2")[0]
    long = "Generate 1 video on Pixio for " + "9" * 600 + " credits"
    a3, _ = reg.request("t", {}, session_id="s3", cost_note=long)
    note = reg.pending_for("s3")[0]["cost_note"]
    assert len(note) <= COST_NOTE_MAX and note.endswith("…")
    for a in (a1, a2, a3):
        reg.pop(a)


async def test_a_secret_shaped_word_in_the_sentence_is_masked():
    reg = ChatApprovals()
    aid, _ = reg.request("t", {}, session_id="s", cost_note="Generate 1 image; key sk-ant-abcdefghijklmnopqrstuvwxyz123456")
    note = reg.pending_for("s")[0]["cost_note"]
    assert "abcdefghijklmnopqrstuvwxyz123456" not in note
    reg.pop(aid)


@pytest.mark.parametrize("missing", ["registry", "pending_for"])
def test_the_readers_still_answer_none_on_a_bare_platform(missing):
    plat = SimpleNamespace() if missing == "registry" else SimpleNamespace(approvals=SimpleNamespace())
    assert _waiting_on(SimpleNamespace(platform=plat), "s1") is None
