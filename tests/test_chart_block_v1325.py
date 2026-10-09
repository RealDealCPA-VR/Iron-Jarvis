"""The chart fence reaches the model, and both sides name the same words (v1.325.0).

THE SAME THREE-PARTY AGREEMENT AS THE DRAFT FENCE (``test_draft_card_v1161``),
and every party fails SILENTLY:

1. The daemon tells the model to put chartable numbers in a ```chart fence
   holding JSON of one exact shape (``CHART_BLOCK``).
2. The model emits that fence.
3. The dashboard turns that fence into a chart card (``dashboard/lib/
   chartSpec.ts``: ``CHART_FENCE``, ``CHART_TYPES``, ``parseChartSpec``).

Rename the fence, drop or add a chart type, or rename a key on either side and
the model writes JSON nobody draws: a grey code block, no error anywhere. And
chat has TWO lanes (``chat_turn.run_chat_turn`` and the streaming mirror in
``routes/chat.py``), so an instruction that reaches one of them reads as a
flaky feature. Both are asserted here — the second one through the REAL app.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import CHART_BLOCK, DRAFT_BLOCK, run_chat_turn
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from tests.test_chat_turn_stop_v1241 import _asgi_post, _drive_stream

_ROOT = Path(__file__).resolve().parents[1]
_CHART_SPEC = _ROOT / "dashboard" / "lib" / "chartSpec.ts"
_STREAM_LANE = _ROOT / "src" / "iron_jarvis" / "daemon" / "routes" / "chat.py"


def _read(path: Path) -> str:
    # Normalised at the READER: CI's Windows runners check files out CRLF
    # (the v1.232.1 rule).
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _spec_source() -> str:
    assert _CHART_SPEC.is_file(), (
        "dashboard/lib/chartSpec.ts is missing — the dashboard half of the "
        "chart agreement does not exist, so every ```chart fence renders as code"
    )
    return _read(_CHART_SPEC)


def _spec_fence() -> str:
    m = re.search(r"CHART_FENCE\s*=\s*[\"']([^\"']+)[\"']", _spec_source())
    assert m, "CHART_FENCE is no longer a string literal in chartSpec.ts — update this test"
    return m.group(1)


def _spec_types() -> set[str]:
    m = re.search(r"CHART_TYPES\s*=\s*\[(.*?)\]", _spec_source(), re.S)
    assert m, "CHART_TYPES is no longer an array literal in chartSpec.ts — update this test"
    return set(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))


def _block_fences() -> set[str]:
    return set(re.findall(r"```([a-z]+)", CHART_BLOCK))


def _block_types() -> set[str]:
    m = re.search(r'"type":\s*((?:"\w+"\s*\|?\s*)+)', CHART_BLOCK)
    assert m, "CHART_BLOCK no longer spells the type alternatives as \"a\"|\"b\""
    return set(re.findall(r'"(\w+)"', m.group(1)))


def _block_keys() -> set[str]:
    return set(re.findall(r'"(\w+)":', CHART_BLOCK))


# --------------------------------------------------------------------------- #
# 1. The words agree with the dashboard's parser.
# --------------------------------------------------------------------------- #


def test_the_fence_word_is_the_one_the_dashboard_renders():
    assert _block_fences() == {_spec_fence()}, (
        f"CHART_BLOCK names {sorted(_block_fences())}, chartSpec.ts renders "
        f"{_spec_fence()!r} — the model would emit a fence nobody draws"
    )


def test_the_chart_types_agree_both_ways():
    """A type the block offers and the parser refuses is a code block; a type
    the parser draws and the block never offers is a feature nobody can use."""
    named, drawn = _block_types(), _spec_types()
    assert named, "CHART_BLOCK offers no chart type at all"
    assert named - drawn == set(), f"offered but not drawn: {sorted(named - drawn)}"
    assert drawn - named == set(), f"drawn but never offered: {sorted(drawn - named)}"


def test_every_key_the_block_names_is_one_the_parser_reads():
    keys = _block_keys()
    # The shape the contract fixes — asserted so a block that silently lost
    # a key (and so taught the model a smaller JSON) fails here too.
    assert {"type", "title", "labels", "series", "name", "values"} <= keys, sorted(keys)
    src = _spec_source()
    for key in keys:
        # A property read (`.labels`, `["labels"]`) or a declared field
        # (`labels: string[]`, `title?: string`) — not just the word anywhere.
        pat = rf"(?:\.|\[\s*[\"']){key}\b|\b{key}\??\s*:"
        assert re.search(pat, src), f"chartSpec.ts never reads or declares {key!r}"


# --------------------------------------------------------------------------- #
# 2. It reaches BOTH lanes, before the planner, once.
# --------------------------------------------------------------------------- #


def test_both_lanes_append_it_beside_the_draft_block_before_the_planner():
    # The ADDITION, not the name (the name is also in the import line).
    turn = inspect.getsource(run_chat_turn)
    stream = _read(_STREAM_LANE)
    for label, src in (("POST /chat", turn), ("POST /chat/stream", stream)):
        assert len(re.findall(r"system\s*\+=\s*CHART_BLOCK\b", src)) == 1, label
        add = re.search(r"system\s*\+=\s*CHART_BLOCK\b", src).start()
        draft = re.search(r"system\s*\+=\s*DRAFT_BLOCK\b", src).start()
        planner = src.index("plan = _plan_context(")
        assert draft < add < planner, (
            f"{label}: CHART_BLOCK must sit at the DRAFT_BLOCK seam, before the "
            "budget planner (a section added after it is a cost the budget cannot see)"
        )


def test_the_instruction_stays_short_and_says_never_to_invent():
    """Charged on EVERY chat request: three short sentences at most, and the
    rule that makes a chart honest is one of them."""
    assert len(CHART_BLOCK) < 600, f"{len(CHART_BLOCK)} chars"
    body = CHART_BLOCK.strip().split("\n", 1)[1]
    sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", body.strip()) if s]
    assert len(sentences) <= 3, sentences
    assert "never" in CHART_BLOCK.lower() and "invent" in CHART_BLOCK.lower()
    # Its own section — a fence rule folded into the draft section would be
    # read as more email advice.
    assert CHART_BLOCK.startswith("\n\n# ") and "```chart" not in DRAFT_BLOCK


def _app(tmp_path):
    return create_app(str(tmp_path))


@pytest.mark.asyncio
async def test_the_post_lane_sends_it_exactly_once(tmp_path):
    app = _app(tmp_path)
    systems: list[str] = []

    async def fake_complete(**kw):
        systems.append(kw["system"])
        return RouteResult(LLMResponse(text="ok", usage={}), "ollama", "llama-local")

    app.state.platform.router.complete = fake_complete
    status, out = await _asgi_post(
        app, "/chat", {"messages": [{"role": "user", "content": "chart my sales"}]}
    )
    assert status == 200, out
    assert systems and systems[0].count(CHART_BLOCK) == 1


@pytest.mark.asyncio
async def test_the_stream_lane_sends_it_exactly_once(tmp_path):
    app = _app(tmp_path)
    systems: list[str] = []

    async def fake_stream(*, system, messages, tools, **kw):
        systems.append(system)
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok", usage={}),
               "provider": "ollama", "model": "llama-local"}

    app.state.platform.router.stream = fake_stream
    frames = await _drive_stream(
        app, {"messages": [{"role": "user", "content": "chart my sales"}]}, None
    )
    assert any(ev == "done" for ev, _ in frames), frames
    assert systems and systems[0].count(CHART_BLOCK) == 1
