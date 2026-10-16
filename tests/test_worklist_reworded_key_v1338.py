"""v1.338.0 — ``worklist_done`` accepts the key the model actually sends.

The live ledger (30 days, the user's install): 24 of 160 ``worklist_done``
calls were refused with "is not on this team's worklist". Every one had the
same shape — the model copied ``worklist_next``'s whole listing line,
``<key> — <label>``, as the key:

    sent:   history://chat/2026-09-12/explain what KV cache is — chat 2026-09-12 — explain what KV cache is
    stored: history://chat/2026-09-12/explain what KV cache is

The item was finished and the record said otherwise. Now an exact miss falls
back to the ONE stored key the text starts with, followed by whitespace and a
separator; anything less certain is still refused, and the refusal names the
closest keys.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.worklist.models import DONE, PENDING

BOARD = "root-session"
KV = "history://chat/2026-09-12/explain what KV cache is"


@pytest.fixture
def platform(tmp_path):
    return build_platform(str(tmp_path))


@pytest.fixture
def store(platform):
    return platform.worklist


def test_the_listing_line_copied_whole_finds_its_item(store):
    store.add(BOARD, [(KV, "chat 2026-09-12")])
    row = store.finish(BOARD, f"{KV} — chat 2026-09-12 — explain what KV cache is", status=DONE)
    assert row is not None and row.key == KV and row.status == DONE


@pytest.mark.parametrize("tail", [" — label", " – label", " - label", " -- label", " | label", ": label"])
def test_each_separator_a_model_writes(store, tail):
    store.add(BOARD, [("C:/tax/w2.pdf", "")])
    assert store.finish(BOARD, "C:/tax/w2.pdf" + tail, status=DONE) is not None


@pytest.mark.parametrize("other", ["C:/tax/w2 3.pdf", "C:/tax/w2.pdf-2", "C:/tax/w2.pdf 3", "C:/tax/w2.pdf.bak"])
def test_another_name_is_never_taken_for_the_item(store, other):
    """A different item that merely STARTS with a stored key is not it."""
    store.add(BOARD, [("C:/tax/w2.pdf", "")])
    assert store.finish(BOARD, other, status=DONE) is None
    assert store.get(BOARD, "C:/tax/w2.pdf").status == PENDING


def test_the_longest_stored_key_wins(store):
    # Both stored keys fit the sent text (each followed by " — "): the longer,
    # more specific one is the item the listing line was about.
    store.add(BOARD, [("C:/f/report", ""), ("C:/f/report — final", "")])
    row = store.finish(BOARD, "C:/f/report — final — the label", status=DONE)
    assert row.key == "C:/f/report — final"
    assert store.get(BOARD, "C:/f/report").status == PENDING


def test_an_exact_key_is_unchanged(store):
    store.add(BOARD, [("C:/f/a.pdf", ""), ("C:/f/a.pdf — copy", "")])
    # The exact key wins over any reworded reading of it.
    assert store.finish(BOARD, "C:/f/a.pdf — copy", status=DONE).key == "C:/f/a.pdf — copy"
    assert store.get(BOARD, "C:/f/a.pdf").status == PENDING


def test_another_boards_item_is_never_matched(store):
    store.add("other-board", [(KV, "")])
    assert store.finish(BOARD, f"{KV} — chat", status=DONE) is None


def test_the_refusal_names_the_closest_keys(platform, tmp_path):
    from iron_jarvis.worklist.tools import WorklistDoneTool

    ctx = ToolContext(workspace=Path(tmp_path), session_id="s1", agent_run_id="r1",
                      config=platform.config, event_bus=platform.event_bus, engine=platform.engine)
    board = platform.worklist.board_id_for(ctx.session_id, ctx.agent_run_id, ctx.workspace)
    platform.worklist.add(board, [("C:/tax/1099-INT Vanguard 2025.pdf", "")])
    tool = WorklistDoneTool(platform.worklist)
    res = asyncio.run(tool.execute({"key": "C:/tax/1099-INT Vanguard 2025 .pdf", "status": "done"}, ctx))
    assert res.ok is False
    assert "Did you mean: 'C:/tax/1099-INT Vanguard 2025.pdf'?" in res.error
    assert res.data["did_you_mean"] == ["C:/tax/1099-INT Vanguard 2025.pdf"]
    # …and the reworded form of the same key is simply accepted.
    ok = asyncio.run(tool.execute(
        {"key": "C:/tax/1099-INT Vanguard 2025.pdf — Vanguard 1099", "status": "done"}, ctx))
    assert ok.ok is True and ok.data["item"]["key"] == "C:/tax/1099-INT Vanguard 2025.pdf"
