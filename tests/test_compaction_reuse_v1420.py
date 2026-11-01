"""v1.420.0 (C3.5): a conversation past the ceiling is summarised ONCE, not
on every message.

THE LOOP THIS CONFIRMS AND ENDS. The chat's automatic summary was cached under
an exact hash of the covered prefix (``compaction.prefix_key`` over every
message but the newest ``KEEP_RECENT``). Each new message moves that prefix
by two lines, so the next turn's key never matched: every turn past the
ceiling paid one more summarising model call (and, on a Codex session planned
against the 32k default, that was every turn). Measured before the fix with a
scripted summariser over three consecutive turns: three calls.

Now a stored summary whose covered lines are still the conversation's own
opening lines (``CompactionStore.standing`` — content-addressed, so a hit
PROVES the covered lines are byte-identical) is reused until the coverage has
grown past ``KEEP_RECENT`` more lines; only then is a new one written.
Driven through BOTH real chat lanes.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.context import compaction as C
from iron_jarvis.daemon.app import create_app
from tests.test_wave3_chat_lanes_v1311 import LANES, _post_turn

FILLER = "audit the ledger carefully, line by line. " * 12


def _client(tmp_path) -> TestClient:
    client = TestClient(create_app(str(tmp_path)))
    r = client.put("/settings", json={"values": {"model_context_windows": {"mock": 2000}}})
    assert r.status_code == 200, r.text
    return client


def _wire_summariser(platform) -> dict:
    calls = {"complete": 0}

    async def fake_complete(system, user):
        calls["complete"] += 1
        return "GOAL:\n- audit the ledger\n", "acme", "acme-1"

    platform._compaction_complete = lambda *a, **k: fake_complete
    return calls


def _conversation(turns: int) -> list[dict]:
    out = []
    for i in range(turns):
        out.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}: {FILLER}"})
    return out


@pytest.mark.parametrize("lane", LANES)
def test_three_messages_past_the_ceiling_pay_one_summary(tmp_path, lane):
    client = _client(tmp_path)
    calls = _wire_summariser(client.app.state.platform)
    history = _conversation(14)
    seen = []
    for n in range(3):
        msgs = history + [{"role": "user", "content": f"and now the totals, part {n}?"}]
        body, _ = _post_turn(client, lane, {"messages": msgs})
        ctx = body["context"]
        assert ctx.get("compacted") is True, (n, ctx)
        seen.append(ctx.get("covers"))
        # The next message: this question and a long answer join the history.
        history = msgs + [{"role": "assistant", "content": f"answer {n}: {FILLER}"}]
    assert calls["complete"] == 1, f"{lane}: one summary for three messages, not {calls['complete']}"
    assert len(set(seen)) == 1, f"{lane}: the same summary (same coverage) served every turn: {seen}"


@pytest.mark.parametrize("lane", LANES)
def test_a_new_summary_once_the_coverage_has_grown_past_keep_recent(tmp_path, lane):
    client = _client(tmp_path)
    calls = _wire_summariser(client.app.state.platform)
    history = _conversation(14)
    for n in range(C.KEEP_RECENT // 2 + 2):
        msgs = history + [{"role": "user", "content": f"part {n}?"}]
        _post_turn(client, lane, {"messages": msgs})
        history = msgs + [{"role": "assistant", "content": f"answer {n}: {FILLER}"}]
    assert calls["complete"] == 2, f"{lane}: the coverage grew past KEEP_RECENT once ({calls})"


@pytest.mark.parametrize("lane", LANES)
def test_a_changed_opening_is_never_served_the_old_summary(tmp_path, lane):
    """Content addressing: an edited early message is a different
    conversation, so its summary is written anew."""
    client = _client(tmp_path)
    calls = _wire_summariser(client.app.state.platform)
    history = _conversation(14)
    _post_turn(client, lane, {"messages": history + [{"role": "user", "content": "totals?"}]})
    edited = [dict(m) for m in history]
    edited[0]["content"] = "turn 0 (edited): " + FILLER
    _post_turn(client, lane, {"messages": edited + [{"role": "user", "content": "totals?"}]})
    assert calls["complete"] == 2
