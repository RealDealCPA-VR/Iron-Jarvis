"""The intelligence plan, release 9: two new measurements.

HOLD (how much a model holds) and SPEED (how fast it writes), run by Measure
and by the idle trial. Made-up text only; scored mechanically against a fake
transport, the clock injected (never a sleep).

Review fixes pinned here: HOLD learns how many characters of its made-up text
make a token (never assumes English) and says "about" only when a LARGER size
really failed; SPEED times only a fresh, believable story with no hidden
thinking; a re-measure that finds nothing clears the old line.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from iron_jarvis.envelope import store
from iron_jarvis.envelope.plain import plain_lines
from iron_jarvis.envelope.probes import (
    HOLD_CAP_TOKENS,
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
_HERO = re.compile(r"named (\w+)")
#: The stand-in server's own tokenizer: made-up words are 3 characters a token.
CPT = 3.0


def _code_in(messages) -> str:
    return _CODE.search(messages[-1]["content"]).group(1)


def _sent(messages) -> int:
    return sum(len(m["content"]) for m in messages)


def _holding(max_chars: int, *, usage: bool = True, cut: bool = False, error_above: int | None = None):
    """A model that reads back the code word while the text fits ``max_chars``.
    Its server reports prompt tokens at :data:`CPT` (``usage``); ``cut``: past
    the limit it still answers but reports a cut prompt; ``error_above``: a
    prompt larger than that is refused with an error."""
    seen: list[int] = []

    async def complete(messages, **kw):
        size = len(messages[-1]["content"])
        seen.append(size)
        if error_above is not None and size > error_above:
            raise RuntimeError("400 maximum context length")
        tokens = {"prompt_tokens": int(_sent(messages) / CPT)} if usage else {}
        if size <= max_chars:
            return ProbeReply(text=_code_in(messages), usage=tokens)
        if cut:
            return ProbeReply(text=_code_in(messages), usage={"prompt_tokens": 2048})
        return ProbeReply(text="I do not know.", usage=tokens)

    complete.seen = seen  # type: ignore[attr-defined]
    return complete


def first_size(limit: int) -> int:
    return int(min(limit, HOLD_CAP_TOKENS) * 0.75 * CPT)


def test_made_up_text_is_made_up_and_deterministic():
    a = made_up_text(5000, seed=3)
    assert a == made_up_text(5000, seed=3)
    assert len(a) == 5000
    assert a != made_up_text(5000, seed=4)
    assert not re.search(r"\b(the|and|client|tax|invoice)\b", a)


def test_hold_sizes_by_the_tokens_its_made_up_text_really_takes():
    complete = _holding(10**9)
    result = asyncio.run(HoldProbe(limit_tokens=16000).run(complete))
    # Sized at 3 characters a token, as the server counted, not English's 4.
    assert result.scores["holds_chars"] == pytest.approx(first_size(16000), rel=0.01)


def test_hold_says_at_least_when_nothing_larger_was_tried():
    # Read back at the first size: nothing larger failed, so "at least" —
    # whether the window is under the cap or over it.
    for limit in (8192, 131072):
        result = asyncio.run(HoldProbe(limit_tokens=limit).run(_holding(10**9)))
        assert result.scores["holds_more"] == 1, limit


def test_hold_says_about_only_when_a_larger_size_really_failed():
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(_holding(30000)))
    assert result.scores["holds_chars"] == pytest.approx(first_size(131072) / 4, rel=0.01)
    assert result.scores["holds_more"] == 0


def test_a_prompt_the_server_cut_is_a_real_failure():
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(_holding(30000, cut=True)))
    assert result.scores["holds_chars"] == pytest.approx(first_size(131072) / 4, rel=0.01)
    assert result.scores["holds_more"] == 0


def test_a_size_the_server_refused_says_nothing_about_holding():
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(_holding(10**9, error_above=40000)))
    assert result.ok and 0 < result.scores["holds_chars"] <= 40000
    assert result.scores["holds_more"] == 1  # only errors above it: "at least"


def test_without_token_counts_hold_sizes_small_and_says_at_least():
    complete = _holding(30000, usage=False)
    result = asyncio.run(HoldProbe(limit_tokens=131072).run(complete))
    # The first real try (after the calibration try) is sized at 2 characters
    # a token, the small side, not English's 4.
    assert complete.seen[1] < int(HOLD_CAP_TOKENS * 0.75 * 2.0) + 400
    assert result.scores["holds_chars"] == int(HOLD_CAP_TOKENS * 0.75 * 2.0) // 2
    assert result.scores["holds_more"] == 1


def test_hold_reading_nothing_back_clears_and_all_errors_is_a_dead_probe():
    none = asyncio.run(HoldProbe(limit_tokens=None).run(_holding(10**9)))
    assert none.ok and none.scores == {} and not none.cleared
    never = asyncio.run(HoldProbe(limit_tokens=131072).run(_holding(0)))
    assert never.ok and never.scores == {} and never.cleared == {"holds_chars", "holds_more"}

    async def refuses_all(messages, **kw):
        raise RuntimeError("down")

    dead = asyncio.run(HoldProbe(limit_tokens=131072).run(refuses_all))
    assert dead.ok is False and dead.scores == {}


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _writer(clock: FakeClock, words: int, seconds: list[float], *, hero: bool = True, **reply):
    async def complete(messages, **kw):
        clock.t += seconds.pop(0)
        name = _HERO.search(messages[-1]["content"]).group(1) if hero else "Bob"
        return ProbeReply(text=" ".join([name] + ["lighthouse"] * (words - 1)), **reply)

    return complete


def test_speed_is_words_over_the_injected_clock_best_of_its_tries():
    clock = FakeClock()
    result = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [6.0, 30.0])))
    assert result.ok
    assert result.scores == {"words_per_second": 25.0}


def test_speed_asks_for_a_different_story_each_try():
    seen: list[str] = []

    async def complete(messages, **kw):
        seen.append(messages[-1]["content"])
        return ProbeReply(text="x")

    asyncio.run(SpeedProbe(clock=FakeClock()).run(complete))
    assert len(seen) == 2 and seen[0] != seen[1]


def test_speed_times_no_cached_canned_or_thinking_reply():
    clock = FakeClock()
    # A canned answer (not the story asked for), however fast.
    canned = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [5.0, 5.0], hero=False)))
    assert canned.scores == {} and canned.cleared == {"words_per_second"}
    # An unbelievable rate.
    fast = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [0.01, 0.01])))
    assert fast.scores == {}
    # Hidden thinking: reasoning tokens reported, or reasoning text sent apart.
    usage = {"completion_tokens_details": {"reasoning_tokens": 900}}
    thought = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [5.0, 5.0], usage=usage)))
    assert thought.scores == {}
    apart = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 150, [5.0, 5.0], thinking="hmm")))
    assert apart.scores == {}


def test_speed_does_not_time_a_reply_too_short_to_be_a_rate():
    clock = FakeClock()
    result = asyncio.run(SpeedProbe(clock=clock).run(_writer(clock, 5, [1.0, 1.0])))
    assert result.ok and result.scores == {}


def _model(clock: FakeClock):
    async def model(messages, **kw):
        last = messages[-1]["content"]
        if "code word is" in last:
            return ProbeReply(text=_code_in(messages), usage={"prompt_tokens": int(_sent(messages) / CPT)})
        if "lighthouse keeper" in last:
            clock.t += 5.0
            name = _HERO.search(last).group(1)
            return ProbeReply(text=" ".join([name] + ["word"] * 99))
        if kw.get("tools"):
            return ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}])
        if kw.get("response_format"):
            return ProbeReply(text='{"tool": "get_weather", "args": {"city": "Paris", "units": "celsius"}}')
        return ProbeReply(text='{"title": "t", "priority": 1, "done": false, "tags": []}')

    return model


def test_the_trial_battery_saves_both_numbers_as_measured(tmp_path: Path):
    clock = FakeClock()
    battery = trial_battery(limit_tokens=16000, clock=clock)
    base = CapabilityProfile(model_id="m", provider="ollama")
    session = asyncio.run(run_quick_battery(base, _model(clock), probes=battery, home=tmp_path))
    assert session.source == "probed"
    saved = store.load_profile(tmp_path, "ollama", "m")
    assert saved.field_measured("holds_chars") and saved.field_measured("words_per_second")
    assert saved.holds_chars == pytest.approx(first_size(16000), rel=0.01)
    assert saved.words_per_second == 20.0
    assert not saved.field_measured("honest_context")
    assert PAGE_CHARS == 3000


def _good_record(tmp_path: Path) -> CapabilityProfile:
    good = CapabilityProfile(
        model_id="m", provider="ollama", source="probed", probed_at="2026-11-27T00:00:00+00:00",
        holds_chars=60000, words_per_second=40.0,
        measured_fields=["holds_chars", "holds_more", "words_per_second", "tool_protocols.native"],
        tool_protocols={"native": 1.0},
    )
    store.save_profile(tmp_path, good)
    return good


def test_a_remeasure_that_finds_nothing_clears_the_old_lines(tmp_path: Path):
    good = _good_record(tmp_path)

    async def answers_but_holds_nothing(messages, **kw):
        if kw.get("tools"):
            return ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}])
        return ProbeReply(text="six words and that is all")

    battery = trial_battery(limit_tokens=16000, clock=FakeClock())
    asyncio.run(run_quick_battery(good, answers_but_holds_nothing, probes=battery, home=tmp_path))
    saved = store.load_profile(tmp_path, "ollama", "m")
    assert not saved.field_measured("holds_chars") and not saved.field_measured("words_per_second")
    lines = plain_lines(saved)
    assert not any("pages" in line or "words a second" in line for line in lines), lines


def test_a_dead_server_keeps_the_last_good_lines(tmp_path: Path):
    good = _good_record(tmp_path)

    async def down(messages, **kw):
        raise RuntimeError("connection refused")

    battery = trial_battery(limit_tokens=16000, clock=FakeClock())
    asyncio.run(run_quick_battery(good, down, probes=battery, home=tmp_path))
    saved = store.load_profile(tmp_path, "ollama", "m")
    assert saved.holds_chars == 60000 and saved.words_per_second == 40.0


def test_corrupt_numbers_load_as_not_measured():
    p = CapabilityProfile.from_dict(
        {"model_id": "m", "holds_chars": "lots", "holds_more": True, "words_per_second": -3}
    )
    assert (p.holds_chars, p.holds_more, p.words_per_second) == (0, 0, 0.0)
