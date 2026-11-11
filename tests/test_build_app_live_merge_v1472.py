"""v1.472.0: a Build › Code viewer gets a running reply's pieces MERGED.

``HostedTurn.follow`` sent every streamed piece as its own message (the
replay was merged, a live viewer was not), and the page then drew and
folded each one. Now the pieces that arrive within ``LIVE_MERGE_S`` of the
first go out as one message, merged the replay's way (joined per item and
masked again), every word still arrives, and the turn's end still ends the
stream.
"""
from __future__ import annotations

import asyncio
import json

from iron_jarvis.hosted import service as S


def _frames(chunks: list[str]) -> list[dict]:
    out = []
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            for line in block.splitlines():
                if line.startswith("data:"):
                    out.append(json.loads(line[5:].strip()))
    return out


async def _follow(turn: S.HostedTurn, chunks: list[str]) -> None:
    async for chunk in turn.follow():
        chunks.append(chunk)


def _piece(i: int, item: str = "t1") -> dict:
    return {"type": "text", "at": "2026-11-11T10:00:00Z", "item": item, "delta": f"w{i} ", "turn": "turn-1"}


def test_a_burst_of_pieces_reaches_a_viewer_as_one_merged_message() -> None:
    async def main() -> list[str]:
        turn = S.HostedTurn("turn-1", "s1")
        chunks: list[str] = []
        task = asyncio.create_task(_follow(turn, chunks))
        await asyncio.sleep(0)
        for i in range(50):
            turn.add(_piece(i))
        await asyncio.sleep(S.LIVE_MERGE_S * 3)
        turn.end("completed")
        await asyncio.wait_for(task, 5)
        return chunks

    chunks = asyncio.run(main())
    frames = _frames(chunks)
    assert "".join(f["delta"] for f in frames) == "".join(f"w{i} " for i in range(50))
    # Fifty pieces, one message (they all arrived inside one window).
    assert len(chunks) == 1
    assert len(frames) == 1


def test_a_step_after_the_words_keeps_its_place_and_ends_the_wait(monkeypatch) -> None:
    # A window far longer than the test's own bound: the step must end it.
    monkeypatch.setattr(S, "LIVE_MERGE_S", 60.0)

    async def main() -> list[str]:
        turn = S.HostedTurn("turn-1", "s1")
        chunks: list[str] = []
        task = asyncio.create_task(_follow(turn, chunks))
        await asyncio.sleep(0)
        turn.add(_piece(0))
        turn.add(_piece(1))
        turn.add({"type": "tool", "at": "2026-11-11T10:00:01Z", "item": "x1", "kind": "read", "status": "running",
                  "title": "Read a.py", "turn": "turn-1"})
        turn.add(_piece(2, item="t2"))
        async def first() -> None:
            while not chunks:
                await asyncio.sleep(0.001)

        await asyncio.wait_for(first(), 30)
        turn.end("completed")
        await asyncio.wait_for(task, 5)
        return chunks

    chunks = asyncio.run(main())
    frames = _frames(chunks)
    assert [f["type"] for f in frames] == ["text", "tool", "text"]
    assert frames[0]["delta"] == "w0 w1 "
    assert frames[2]["item"] == "t2"
    # A step ended the 60 s wait (the chunk came inside the 30 s bound).
    assert len(chunks) >= 1


def test_a_merged_message_is_masked_again() -> None:
    """A key split across two pieces was never whole for either one's mask."""
    secret = "sk-ant-api03-" + "A" * 40

    async def main() -> list[str]:
        turn = S.HostedTurn("turn-1", "s1")
        chunks: list[str] = []
        task = asyncio.create_task(_follow(turn, chunks))
        await asyncio.sleep(0)
        turn.add({**_piece(0), "delta": "key " + secret[:20]})
        turn.add({**_piece(1), "delta": secret[20:] + " end"})
        await asyncio.sleep(S.LIVE_MERGE_S * 3)
        turn.end("completed")
        await asyncio.wait_for(task, 5)
        return chunks

    joined = "".join(f["delta"] for f in _frames(asyncio.run(main())))
    assert secret not in joined
    assert joined.endswith(" end")


def test_the_end_of_the_turn_inside_the_window_still_ends_the_stream() -> None:
    async def main() -> list[str]:
        turn = S.HostedTurn("turn-1", "s1")
        chunks: list[str] = []
        task = asyncio.create_task(_follow(turn, chunks))
        await asyncio.sleep(0)
        turn.add(_piece(0))
        turn.end("completed")
        await asyncio.wait_for(task, 5)
        return chunks

    frames = _frames(asyncio.run(main()))
    assert [f["delta"] for f in frames] == ["w0 "]
