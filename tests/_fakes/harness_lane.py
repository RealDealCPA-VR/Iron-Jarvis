"""A scripted model on the REAL chat lanes for IJ harness turns (part 3,
Phase 3) — the shape of ``tests/test_finish_lanes_v1386.py``'s, lifted so
the Phase 3 files share one copy. The registry, the permission engine, the
cards, the ledger, the undo journal and the shell tool are the real ones;
only the model is scripted.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.routes.chat import _approvals
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

WHO = ("anthropic", "claude-sonnet-4-5")
SESSION = "build:phase3-test"


class Model:
    """A move is a list of ToolCalls, a reply text, ``(text, calls)``, an
    ``LLMResponse``, an exception (raised) or a callable ``(model) -> move``.
    Past the script, and on a tool-less call, it answers "Done."."""

    def __init__(self, moves: list, who: tuple[str, str] = WHO) -> None:
        self.moves = list(moves)
        self.calls: list[dict] = []
        self.who = who

    async def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({"system": system, "tools": [t["name"] for t in tools or []],
                           "specs": list(tools or []), "messages": list(messages)})
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if callable(move):
            move = move(self)
            if inspect.isawaitable(move):
                move = await move
        if isinstance(move, BaseException):
            raise move
        if isinstance(move, LLMResponse):
            return move
        usage = {"input_tokens": 3, "output_tokens": 1}
        if isinstance(move, tuple):
            return LLMResponse(text=move[0], tool_calls=list(move[1]), usage=usage)
        if isinstance(move, str):
            return LLMResponse(text=move, usage=usage)
        return LLMResponse(text="", tool_calls=list(move), usage=usage)

    async def stream(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        resp = await self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": self.who[0], "model": self.who[1]}

    async def complete(self, *, provider=None, model=None, system="", messages=(), tools=None, task_class=None, **kw):
        return RouteResult(await self._next(system, messages, tools), self.who[0], self.who[1])

    def tool_messages(self, call_index: int = -1) -> dict[str, str]:
        """tool_call_id -> content of the tool messages the call was sent."""
        return {m.tool_call_id: str(m.content) for m in self.calls[call_index]["messages"] if m.role == "tool"}


def wire(platform: Any, monkeypatch: Any, moves: list, who: tuple[str, str] = WHO) -> Model:
    model = Model(moves, who)
    monkeypatch.setattr(platform.router, "stream", model.stream)
    monkeypatch.setattr(platform.router, "complete", model.complete)
    return model


def body(ws: Path, text: str = "do the task", *, who: tuple[str, str] = WHO, **kw: Any) -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider=who[0], model=who[1], workspace_dir=str(ws),
        approval_mode=kw.pop("approval_mode", "yolo"), **kw,
    )


def frames_of(chunks: list[str]) -> list[tuple[str, dict]]:
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


async def stream(platform: Any, b: ChatBody, *, answer=None, session_id: str = SESSION, **kw: Any) -> dict:
    """The stream lane; its approval cards answered by ``answer(frame)``."""
    approvals = _approvals(SimpleNamespace(platform=platform))
    gen = await stream_chat_turn(
        platform, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
        session_id=session_id, round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
    )
    frames: list[tuple[str, dict]] = []
    async for chunk in gen:
        for event, data in frames_of([chunk]):
            frames.append((event, data))
            if event == "approval":
                decision = answer(data) if answer is not None else None
                assert decision, f"a card nobody expected: {data}"
                approvals.resolve(str(data["id"]), decision)
    done = [d for e, d in frames if e == "done"]
    errors = [d for e, d in frames if e == "error"]
    return {"done": done[0] if done else None, "frames": frames, "errors": errors}


async def post(platform: Any, b: ChatBody, *, session_id: str = SESSION, **kw: Any) -> dict:
    return await run_chat_turn(
        platform, BUILTIN_PERSONAS, b, harness=kw.pop("harness", "generic"),
        session_id=session_id, round_budget=kw.pop("round_budget", 30), stay_in_chat=True, **kw,
    )


def ledger(platform: Any) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation).order_by(ToolInvocation.created_at)).all())
        for r in rows:
            db.expunge(r)
        return rows


def finished_ids(frames: list[tuple[str, dict]]) -> list[str]:
    """Every finished tool_call frame's id — what the benchmark counts as the
    turn's tool calls."""
    return [d["id"] for e, d in frames if e == "tool_call" and d.get("status") == "finished"]


def call(name: str, cid: str, **args: Any) -> ToolCall:
    return ToolCall(id=cid, name=name, arguments=args)


def no_finish_check(ws: Path) -> None:
    """Turn the folder's finish check off (its own switch, ``finish.toml``)
    so a test counts only the calls it is about."""
    h = ws / ".ironjarvis" / "harness"
    h.mkdir(parents=True, exist_ok=True)
    (h / "finish.toml").write_text("enabled = false\n", encoding="utf-8")
