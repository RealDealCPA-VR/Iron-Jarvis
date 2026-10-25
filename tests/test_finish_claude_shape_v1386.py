"""IJ harness part 3, Phase 2 — REVIEW: the finish note's shape on claude-cli.

Not a defect — a measurement, pinned. The lane puts the answer the model
ended with back as an assistant turn carrying its own ``raw_blocks`` (the
``claude_cli_native`` carrier) and the note after it. Through the REAL
adapter and transport against the fake CLI (``fixtures/fake_claude_v1300.py``):
the answer replays from its carrier — signed thinking intact, so the relay's
cache pin keeps reading the prompt from cache — and the note is the one frame
that queries. No live model.
"""

from __future__ import annotations

from iron_jarvis.providers.adapters.base import LLMMessage
from tests.test_claude_native_v1300 import Fake, _clean_env  # noqa: F401 — the fixture


async def test_the_note_follows_the_answer_with_its_signed_thinking(tmp_path, monkeypatch):
    fake = Fake(tmp_path, monkeypatch, scenario="thinking")
    adapter = fake.adapter()
    msgs = [LLMMessage(role="user", content="set X to 2")]
    r1 = await adapter.complete(system="sys", messages=msgs, tools=[])
    assert r1.text == "Hello" and r1.raw_blocks
    msgs = msgs + [LLMMessage(role="assistant", content=r1.text, raw_blocks=list(r1.raw_blocks)),
                   LLMMessage(role="user", content="FINISH CHECK. note")]
    await adapter.complete(system="sys", messages=msgs, tools=[])
    sent = fake.record["frames"]
    assert [f["type"] for f in sent] == ["user", "assistant", "user"]
    assert sent[0]["shouldQuery"] is False and "shouldQuery" not in sent[2]
    assert sent[1]["message"]["content"][0] == {"type": "thinking", "thinking": "hmm", "signature": "sig-1"}
    assert sent[2]["message"]["content"] == [{"type": "text", "text": "FINISH CHECK. note"}]
