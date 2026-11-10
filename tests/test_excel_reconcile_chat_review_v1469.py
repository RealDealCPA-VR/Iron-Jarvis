"""The review of excel_reconcile IN A CHAT (v1.469.0): the result file it
writes is a file the turn made.

Red on the first cut, through BOTH chat lanes:

* a reply that said where the result was saved got "_Note: nothing was
  written to disk this turn ... that file does not exist_" appended, while
  the file sat in the folder (the tool was in no list of tools that write);
* the saved file was not in the turn's ``documents`` (the files a reply made,
  which the chat shows under it);
* under "Ask before every action" the tool wrote that file with no card.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openpyxl import Workbook

from iron_jarvis.core.approvals import flush_interrupt_notes
from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

CLAUDE = ("anthropic", "claude-sonnet-4-5")
D = dt.date
SAID = "The two lists match except for one deposit. I saved the full result as October rec.xlsx."


class Model:
    def __init__(self, moves):
        self.moves = list(moves)

    def _next(self, tools):
        return self.moves.pop(0) if (self.moves and tools) else SAID

    async def stream(self, *, system, messages, tools, provider=None, model=None, **kw):
        move = self._next(tools)
        if isinstance(move, str):
            resp = LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
            yield {"type": "text", "text": move}
        else:
            resp = LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": CLAUDE[0], "model": CLAUDE[1]}

    async def complete(self, *, system, messages, tools, provider=None, model=None, **kw):
        move = self._next(tools)
        if isinstance(move, str):
            return RouteResult(LLMResponse(text=move, usage={}), *CLAUDE)
        return RouteResult(LLMResponse(text="", tool_calls=list(move), usage={}), *CLAUDE)


def _lists(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in (("bank.xlsx", [(D(2026, 10, 1), 100), (D(2026, 10, 2), 250), (D(2026, 10, 9), 75)]),
                       ("gl.xlsx", [(D(2026, 10, 1), 100), (D(2026, 10, 2), 250)])):
        wb = Workbook()
        ws = wb.active
        ws.append(["Date", "Amount"])
        for r in rows:
            ws.append(list(r))
        wb.save(folder / name)


@pytest.fixture()
def lane(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    work = tmp_path / "work"
    _lists(work)
    call = [ToolCall(id="r1", name="excel_reconcile", arguments={
        "a": {"path": "bank.xlsx", "name": "Bank", "amount": "Amount", "date": "Date"},
        "b": {"path": "gl.xlsx", "name": "Books", "amount": "Amount", "date": "Date"},
        "save_as": "October rec",
    })]

    def wire():
        model = Model([call, SAID])
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        return model

    return SimpleNamespace(platform=platform, wire=wire, work=work)


def _body(lane, **kw):
    return ChatBody(messages=[ChatMessageBody(role="user", content="reconcile the bank list against the books")],
                    provider=CLAUDE[0], model=CLAUDE[1], tools=["excel_reconcile"], workspace_dir=str(lane.work), **kw)


def _frames(chunks):
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            event, data = "", []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
            if event:
                out.append((event, json.loads("".join(data)) if data else {}))
    return out


async def _stream(platform, body, answer="once"):
    approvals = chat_routes._approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
    frames = []
    async for chunk in gen:
        for event, data in _frames([chunk]):
            frames.append((event, data))
            if event == "approval" and answer:
                assert approvals.resolve(str(data["id"]), answer)
    await flush_interrupt_notes()
    done = [x for e, x in frames if e == "done"]
    assert done, frames[-4:]
    return {"done": done[0], "cards": [x for e, x in frames if e == "approval"]}


def _check(lane, out) -> None:
    saved = lane.work / "October rec.xlsx"
    assert saved.is_file(), "the tool really wrote the result"
    assert "nothing was written to disk" not in out["reply"], "a file that exists is never called missing"
    assert out["reply"].startswith(SAID)
    assert [Path(p).name for p in out["documents"]] == ["October rec.xlsx"], "the file the turn made is listed under the reply"


async def test_the_post_lane_knows_the_result_file_was_written(lane):
    lane.wire()
    out = await run_chat_turn(lane.platform, BUILTIN_PERSONAS, _body(lane))
    _check(lane, out)


async def test_the_stream_lane_knows_the_result_file_was_written(lane):
    lane.wire()
    out = await _stream(lane.platform, _body(lane))
    _check(lane, out["done"])
    assert out["cards"] == [], "the user picked the tool for this chat: no card in the ordinary posture"


async def test_ask_before_every_action_cards_the_tool_that_can_write_a_file(lane):
    lane.wire()
    out = await _stream(lane.platform, _body(lane, approval_mode="always_ask"), answer="once")
    assert [c["tool"] for c in out["cards"]] == ["excel_reconcile"]
    assert (lane.work / "October rec.xlsx").is_file()


def test_the_lists_of_tools_that_write_know_it():
    assert CT._claimed_write_note(SAID, ["excel_reconcile"]) == ""
    assert CT._claimed_write_note(SAID, ["excel_read"]) != "", "a reply after a tool that writes nothing is still checked"
    assert CT.reported_document_paths("excel_reconcile", {"path": "out/rec.xlsx", "abs_path": "x"}) == ["out/rec.xlsx"]
    assert CT.reported_document_paths("excel_reconcile", {"ties": True}) == [], "no save_as, no file"
    assert not CT._is_office_turn(["excel_reconcile"]) if hasattr(CT, "_is_office_turn") else True
