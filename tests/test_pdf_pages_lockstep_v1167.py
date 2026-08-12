"""The PDF PAGES prompt guidance reaches BOTH chat lanes (v1.167.0).

The block ("prefer pdf_arrange/pdf_split — they write NEW files and never
modify the original") shipped in chat_turn.py only. The dashboard STREAMS, so
the lane users actually watch handled PDF merge/split requests for a whole
wave without the guidance. Same defect class as the v1.161.0 draft-block
lesson: the two lanes are a lock-step pair, and a prompt addition that lands
in one is a feature that flickers.

Pinned the way test_draft_card_v1161 pins DRAFT_BLOCK: assert the ADDITION in
each lane's source, gated on the same armed-tools condition — not the mere
presence of a substring an import line could satisfy.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: The load-bearing phrase, exact in both lanes so the model hears one voice.
_PHRASE = "PDF PAGES: for page-level PDF work"
#: The arming condition that must gate it in both lanes.
_GATE = r'any\(t in \("pdf_arrange", "pdf_split"\) for t in armed\)'


def _lane(name: str) -> str:
    return (_SRC / name).read_text(encoding="utf-8")


def test_non_stream_lane_carries_the_gated_block():
    turn = _lane("chat_turn.py")
    assert _PHRASE in turn
    block_at = turn.index(_PHRASE)
    gate = re.search(_GATE, turn[block_at : block_at + 600])
    assert gate, "the block lost its armed-tools gate in chat_turn.py"


def test_stream_lane_carries_the_same_gated_block():
    stream = _lane("routes/chat.py")
    assert _PHRASE in stream, (
        "the STREAMING lane — the one the dashboard uses — has no PDF PAGES "
        "guidance; a PDF merge/split request runs without it on every real turn"
    )
    block_at = stream.index(_PHRASE)
    gate = re.search(_GATE, stream[block_at : block_at + 600])
    assert gate, "the block lost its armed-tools gate in routes/chat.py"


def test_the_two_lanes_say_exactly_the_same_thing():
    """Wording drift between lanes = two models hearing two rules."""
    pat = re.compile(r'"\\nPDF PAGES: for page-level PDF work \(merge/split/rotate/"')
    assert pat.search(_lane("chat_turn.py")) and pat.search(_lane("routes/chat.py"))
