"""v1.349.0 (ship review): a step's detail masks a WINDOW, never the whole output.

``chat_turn.step_detail`` and ``prior_tool_result_entry`` run on the event
loop in the /chat/stream lane for every finished call. Both masked the WHOLE
tool output (and the whole args JSON — a ``write_file`` call's content can
be megabytes) before keeping a few hundred characters: the wave B re-review
measured ~130 ms per MB, twice per call. They now mask only
``MASK_WINDOW_CHARS`` and cut after — the shown head is still masked whole.
"""

from __future__ import annotations

from iron_jarvis.daemon import chat_turn
from iron_jarvis.detections import redact


def _spy(monkeypatch) -> list[int]:
    seen: list[int] = []
    real = redact.mask

    def spy(value):
        seen.append(len(str(value)))
        return real(value)

    monkeypatch.setattr(redact, "mask", spy)
    return seen


def test_a_huge_output_is_masked_through_a_window_and_the_head_still_masked(monkeypatch):
    seen = _spy(monkeypatch)
    huge = "token=sk-ant-REALSECRET123456 leaked here\n" + ("x" * 2_000_000)
    args = {"path": "big.txt", "content": "y" * 2_000_000}
    detail = chat_turn.step_detail("read_file", args, huge)
    row = chat_turn.prior_tool_result_entry("read_file", args, True, huge)
    assert seen, "mask was never called"
    assert max(seen) <= chat_turn.MASK_WINDOW_CHARS, max(seen)
    # The shown heads are masked (the secret sits inside the window).
    for text in (detail["output"], row["output_head"]):
        assert "REALSECRET" not in text and "sk-ant-" not in text
    assert len(detail["output"]) == chat_turn.STEP_DETAIL_OUTPUT_CHARS
    # The two helpers still agree on the output they show.
    assert row["output_head"].startswith(detail["output"][: len(row["output_head"])])


def test_the_window_is_far_wider_than_anything_shown():
    from iron_jarvis.daemon.schemas import PRIOR_RESULT_ARGS_CHARS, PRIOR_RESULT_OUTPUT_CHARS

    widest_cap = max(
        chat_turn.STEP_DETAIL_INPUT_CHARS,
        chat_turn.STEP_DETAIL_OUTPUT_CHARS,
        PRIOR_RESULT_ARGS_CHARS,
        PRIOR_RESULT_OUTPUT_CHARS,
    )
    assert chat_turn.MASK_WINDOW_CHARS >= 8 * widest_cap
