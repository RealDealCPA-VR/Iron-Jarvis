"""v1.366.0 (IJ harness Phase 3, review): HARNESS OFF IS BYTE-IDENTICAL.

``harness=`` is a keyword on both chat lanes, and every turn that does not
pass it must send exactly what it sent before Phase 3 existed. The doer
checked that once by hand; this file makes it a pin.

``fixtures/harness_off_golden_v1366.json`` was CAPTURED FROM THE PRE-PHASE-3
CODE (commit 8867eae8, the doer's parent: no ``harness`` package beyond the
Phase 1 tools, no ``harness=`` keyword), by running this very file against
that tree with ``IJ_REGEN_HARNESS_OFF=1``. For a scripted model it holds, per
lane (stream / POST) and per scenario (a turn with an armed tool and a tool
round; a plain question): every model call's system prompt, the FULL tool
specs, the messages, and the turn's route object (keys AND their order), the
reply, ``tools_used``, the response's keys and the stream's frame kinds.

The current code, with ``harness`` omitted, must reproduce it byte for byte
(after replacing the run's temporary folder and the home folder with
placeholders). A failure here means a change reached the chat turn that does
NOT run in harness mode. Regenerate ONLY for an intended change to the
non-harness lanes (``IJ_REGEN_HARNESS_OFF=1 uv run pytest
tests/test_harness_off_golden_v1366.py``), and say so in the commit.

The second pin does not depend on the fixture: with ``harness`` omitted, the
harness code is never entered at all (its builder raises if it is).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

FIXTURE = Path(__file__).parent / "fixtures" / "harness_off_golden_v1366.json"
REGEN = os.environ.get("IJ_REGEN_HARNESS_OFF") == "1"
WHO = ("anthropic", "claude-sonnet-4-5")


def _frames(chunks: list[str]) -> list[tuple[str, dict]]:
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


class _Model:
    """A scripted model on the router's ``stream()`` and ``complete()``."""

    def __init__(self, moves: list) -> None:
        self.moves = list(moves)
        self.calls: list[dict[str, Any]] = []

    def _next(self, system, messages, tools) -> LLMResponse:
        self.calls.append({
            "system": system,
            "specs": list(tools or []),
            "messages": [
                {
                    "role": m.role, "content": m.content, "name": getattr(m, "name", None),
                    "tool_call_id": getattr(m, "tool_call_id", None),
                    "tool_calls": [[c.id, c.name, c.arguments] for c in (getattr(m, "tool_calls", None) or [])],
                }
                for m in messages
            ],
        })
        move = self.moves.pop(0) if (self.moves and tools) else "Done."
        if isinstance(move, str):
            return LLMResponse(text=move, usage={"input_tokens": 3, "output_tokens": 1})
        return LLMResponse(text="", tool_calls=list(move), usage={"input_tokens": 3, "output_tokens": 1})

    async def stream(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        resp = self._next(system, messages, tools)
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": WHO[0], "model": WHO[1]}

    async def complete(self, *, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        return RouteResult(self._next(system, messages, tools), *WHO)


_SCENARIOS: dict[str, dict[str, Any]] = {
    # A tool turn bound to a folder: the armed tool, one tool round, the reply.
    "armed_read": {
        "text": "fix the bug in a.py", "tools": ["read_file"], "workspace": True,
        "moves": lambda: [[ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})]],
    },
    # A plain question: no folder, nothing armed by name.
    "plain": {"text": "what is a closure in python?", "tools": None, "workspace": False, "moves": list},
}


def _normalise(value: Any, subs: list[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        for raw, mark in subs:
            value = value.replace(raw, mark)
        return value.replace("<TMP>\\", "<TMP>/")
    if isinstance(value, list):
        return [_normalise(v, subs) for v in value]
    if isinstance(value, dict):
        return {k: _normalise(v, subs) for k, v in value.items()}
    return value


async def _capture(tmp_path: Path, monkeypatch, **harness_kw) -> dict[str, Any]:
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "a.py").write_text("x = 1\n", encoding="utf-8")
    out: dict[str, Any] = {}
    for lane in ("stream", "post"):
        for name, sc in _SCENARIOS.items():
            model = _Model(sc["moves"]())
            monkeypatch.setattr(platform.router, "stream", model.stream)
            monkeypatch.setattr(platform.router, "complete", model.complete)
            kw: dict[str, Any] = {}
            if sc["tools"] is not None:
                kw["tools"] = sc["tools"]
            if sc["workspace"]:
                kw["workspace_dir"] = str(ws)
            body = ChatBody(
                messages=[ChatMessageBody(role="user", content=sc["text"])],
                provider=WHO[0], model=WHO[1], approval_mode="yolo", **kw,
            )
            if lane == "stream":
                gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body, **harness_kw)
                frames = _frames([c async for c in gen])
                done = [d for e, d in frames if e == "done"][0]
                kinds = [e for e, _ in frames]
            else:
                done = await run_chat_turn(platform, BUILTIN_PERSONAS, body, **harness_kw)
                kinds = []
            route = done.get("route") or {}
            out[f"{lane}:{name}"] = {
                "calls": model.calls,
                "route": route,
                "route_order": list(route),
                "reply": done.get("reply"),
                "tools_used": done.get("tools_used"),
                "keys": sorted(done),
                "frames": kinds,
            }
    # Every spelling of the two folders a prompt may carry: as given, resolved
    # (a CI runner's TEMP can be an 8.3 short name, RUNNER~1) and with either
    # separator. The temporary folder first: it usually sits under home.
    subs = []
    for root, mark in ((tmp_path, "<TMP>"), (Path.home(), "<HOME>")):
        for form in dict.fromkeys((root, root.resolve())):
            subs += [(str(form), mark), (form.as_posix(), mark)]
    # json round trip: what the fixture holds is exactly what JSON keeps.
    return json.loads(json.dumps(_normalise(out, subs), default=str))


async def test_a_turn_without_harness_sends_what_it_sent_before_phase_3(tmp_path, monkeypatch):
    got = await _capture(tmp_path, monkeypatch)
    if REGEN:
        FIXTURE.write_text(json.dumps(got, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip("fixture regenerated")
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert set(got) == set(golden)
    for key in sorted(golden):
        want, have = golden[key], got[key]
        assert len(have["calls"]) == len(want["calls"]), key
        for i, (w, h) in enumerate(zip(want["calls"], have["calls"])):
            assert h["system"] == w["system"], f"{key} call {i}: the system prompt changed"
            assert h["specs"] == w["specs"], f"{key} call {i}: the tool specs changed"
            assert h["messages"] == w["messages"], f"{key} call {i}: the messages changed"
        assert have["route_order"] == want["route_order"], f"{key}: the route object's keys or order changed"
        assert have == want, key


@pytest.mark.parametrize("kw", [{}, {"harness": None}, {"harness": False}])
async def test_off_never_enters_the_harness_code(tmp_path, monkeypatch, kw):
    """Structural, fixture-free: an off turn never builds a harness turn, never
    translates, never renders a tree. Every harness entry point raises here."""
    from iron_jarvis.harness import components, mode

    def boom(*a, **k):
        raise AssertionError("harness code ran on a turn with the harness off")

    monkeypatch.setattr(chat_turn, "_build_harness", boom)
    monkeypatch.setattr(mode, "build", boom)
    monkeypatch.setattr(mode.HarnessTurn, "translate_calls", boom)
    monkeypatch.setattr(components, "read_project_layer", boom)
    monkeypatch.setattr(components, "render", boom)
    got = await _capture(tmp_path, monkeypatch, **kw)
    assert all("harness" not in v["route"] for v in got.values())
