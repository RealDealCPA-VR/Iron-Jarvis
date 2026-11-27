"""The intelligence plan, release 9: two new measurements.

HOLD (how much a model holds) and SPEED (how fast it writes), run by Measure
and by the idle trial. Made-up text only; scored mechanically against a fake
transport, the clock injected (never a sleep).
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from iron_jarvis.envelope import store
from iron_jarvis.envelope.probes import (
    HOLD_CAP_TOKENS,
    HOLD_MIN_CHARS,
    PAGE_CHARS,
    HoldProbe,
    ProbeReply,
    SpeedProbe,
    made_up_text,
    trial_battery,
)
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.envelope.runner import run_quick_battery

_CODE = re.compile(r"The code word is (\S+)\.")


def _code_in(messages) -> str:
    return _CODE.search(messages[-1]["content"]).group(1)


def _holding(max_chars: int, *, usage_cut: bool = False):
    """A model that reads back the code word while the text fits ``max_chars``;
    past that it answers something else (or, ``usage_cut``, the code word
    while its server reports a cut prompt)."""
    seen: list[int] = []

    async def complete(messages, **kw):
        size = len(messages[-1]["content"])
        seen.append(size)
        if size <= max_chars:
            return ProbeReply(text=_code_in(messages))
        if usage_cut:
            return ProbeReply(text=_code_in(messages), usage={"prompt_tokens": 2048})
        return ProbeReply(text="I do not know.")

    complete.seen = seen  # type: ignore[attr-defined]
    return complete


def test_made_up_text_is_made_up_and_deterministic():
    a = made_up_text(5000, seed=3)
    assert a == made_up_text(5000, seed=3)
    assert len(a) == 5000
    assert a != made_up_text(5000, seed=4)
    # No real English word of any length the user could have written: the
    # vocabulary is syllables only.
    assert not re.search(r"\b(the|and|client|tax|invoice)\b", a)


def test_hold_reads_back_the_whole_window_when_it_fits():
    complete = _holding(10**9)
    result = asyncio.run(HoldProbe(limit_tokens=16000).run(complete))
    assert result.ok
    assert result.scores["holds_chars"] == int(16000 * 0.75 * 4.0)
    assert result.scores["holds_more"] == 0  # the whole window the app uses
    assert len(complete.seen) == 1


def test_hold_says_at_least_when_the_window_is_larger_than_the_cap():
    complete = _holding(10**9)
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(complete))
    assert result.scores["holds_chars"] == int(HOLD_CAP_TOKENS * 0.75 * 4.0)
    assert result.scores["holds_more"] == 1


def test_hold_halves_until_a_size_is_read_back_and_that_size_is_bracketed():
    complete = _holding(30000)
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(complete))
    first = int(HOLD_CAP_TOKENS * 0.75 * 4.0)
    assert result.scores["holds_chars"] == first // 4
    assert result.scores["holds_more"] == 0  # a larger try failed: "about"


def test_a_prompt_the_server_cut_is_a_failed_size_even_when_the_answer_is_right():
    complete = _holding(30000, usage_cut=True)
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(complete))
    assert result.scores["holds_chars"] == int(HOLD_CAP_TOKENS * 0.75 * 4.0) // 4


def test_hold_measures_nothing_without_a_window_or_when_nothing_is_read_back():
    nothing = asyncio.run(HoldProbe(limit_tokens=None).run(_holding(10**9)))
    assert nothing.ok and nothing.scores == {}
    never = asyncio.run(HoldProbe(limit_tokens=131072).run(_holding(0)))
    assert never.ok and never.scores == {}


def test_a_refused_size_is_a_failed_size_and_only_all_refused_is_a_dead_probe():
    calls = {"n": 0}

    async def refuses_big(messages, **kw):
        calls["n"] += 1
        if len(messages[-1]["content"]) > 40000:
            raise RuntimeError("400 maximum context length")
        return ProbeReply(text=_code_in(messages))

    result = asyncio.run(HoldProbe(limit_tokens=131072).run(refuses_big))
    assert result.ok and 0 < result.scores["holds_chars"] <= 40000

    async def refuses_all(messages, **kw):
        raise RuntimeError("down")

    dead = asyncio.run(HoldProbe(limit_tokens=131072).run(refuses_all))
    assert dead.ok is False and dead.scores == {}


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _writer(clock: FakeClock, words: int, seconds: list[float]):
    async def complete(messages, **kw):
        clock.t += seconds.pop(0)
        return ProbeReply(text=" ".join(["lighthouse"] * words))

    return complete


def test_speed_is_words_over_the_injected_clock_best_of_its_tries():
    clock = FakeClock()
    result = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [6.0, 30.0])))
    assert result.ok
    assert result.scores == {"words_per_second": 25.0}


def test_speed_does_not_time_a_reply_too_short_to_be_a_rate():
    clock = FakeClock()
    result = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 5, [1.0, 1.0])))
    assert result.ok and result.scores == {}


def test_the_trial_battery_saves_both_numbers_as_measured(tmp_path: Path):
    clock = FakeClock()

    async def model(messages, **kw):
        last = messages[-1]["content"]
        if "code word is" in last:
            return ProbeReply(text=_code_in(messages))
        if "lighthouse keeper" in last:
            clock.t += 5.0
            return ProbeReply(text=" ".join(["word"] * 100))
        if kw.get("tools"):
            return ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}])
        if kw.get("response_format"):
            return ProbeReply(text='{"tool": "get_weather", "args": {"city": "Paris", "units": "celsius"}}')
        return ProbeReply(text='{"title": "t", "priority": 1, "done": false, "tags": []}')

    battery = trial_battery(limit_tokens=16000, clock=clock)
    base = CapabilityProfile(model_id="m", provider="ollama")
    session = asyncio.run(run_quick_battery(base, model, probes=battery, home=tmp_path))
    assert session.source == "probed"
    saved = store.load_profile(tmp_path, "ollama", "m")
    assert saved.field_measured("holds_chars") and saved.field_measured("words_per_second")
    assert saved.holds_chars == int(16000 * 0.75 * 4.0)
    assert saved.words_per_second == 20.0
    # The planning window is untouched: holding is said, never planned with.
    assert not saved.field_measured("honest_context")
    assert PAGE_CHARS == 3000


def test_a_failed_remeasure_keeps_the_last_good_numbers(tmp_path: Path):
    good = CapabilityProfile(
        model_id="m", provider="ollama", source="probed", probed_at="2026-11-27T00:00:00+00:00",
        holds_chars=36000, words_per_second=22.0,
        measured_fields=["holds_chars", "holds_more", "words_per_second"],
        tool_protocols={"native": 1.0},
    )
    store.save_profile(tmp_path, good)

    async def tools_only(messages, **kw):
        if kw.get("tools"):
            return ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}])
        return ProbeReply(text="no")

    battery = trial_battery(limit_tokens=16000, clock=FakeClock())
    asyncio.run(run_quick_battery(good, tools_only, probes=battery, home=tmp_path))
    saved = store.load_profile(tmp_path, "ollama", "m")
    assert saved.holds_chars == 36000 and saved.words_per_second == 22.0
    assert saved.field_measured("holds_chars") and saved.field_measured("words_per_second")


def test_corrupt_numbers_load_as_not_measured():
    p = CapabilityProfile.from_dict(
        {"model_id": "m", "holds_chars": "lots", "holds_more": True, "words_per_second": -3}
    )
    assert (p.holds_chars, p.holds_more, p.words_per_second) == (0, 0, 0.0)
