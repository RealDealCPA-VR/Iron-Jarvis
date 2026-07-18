"""Prometheus parser tests, driven by a REAL capture from the user's hardware.

``tests/fixtures/fleet/vllm_metrics.txt`` is 324 lines scraped off the actual
vLLM box at 100.66.161.52:8888, so the expected values below are facts, not
invented shapes. That capture happens to be the perfect honesty fixture: the
server was idle, so ``num_requests_running``, ``num_requests_waiting`` and
``kv_cache_usage_perc`` are all genuinely ``0.0`` while the token counters are
large. If the parser ever conflated "absent" with "zero", an unreachable node
would look exactly like this healthy idle one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.fleet import prometheus
from iron_jarvis.fleet.prometheus import (
    Sample,
    first,
    index,
    label_map,
    parse_text,
    sum_by,
)

FIXTURE = Path(__file__).parent / "fixtures" / "fleet" / "vllm_metrics.txt"


@pytest.fixture(scope="module")
def idx():
    return index(parse_text(FIXTURE.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
# The real capture
# --------------------------------------------------------------------------


def test_fixture_is_the_real_capture():
    body = FIXTURE.read_text(encoding="utf-8")
    assert "deepseek-v4-flash-dspark" in body
    assert len(parse_text(body)) > 100


@pytest.mark.parametrize(
    "name,expected",
    [
        # Idle server: really zero, really reported.
        ("vllm:num_requests_running", 0.0),
        ("vllm:num_requests_waiting", 0.0),
        ("vllm:kv_cache_usage_perc", 0.0),
        # Cumulative work, including scientific notation (1.227019e+06).
        ("vllm:generation_tokens_total", 1763.0),
        ("vllm:prompt_tokens_total", 1227019.0),
        ("vllm:prefix_cache_queries_total", 1227019.0),
        ("vllm:prefix_cache_hits_total", 91392.0),
    ],
)
def test_real_vllm_metrics_values(idx, name, expected):
    assert sum_by(idx, name) == pytest.approx(expected)
    assert first(idx, name) == pytest.approx(expected)


def test_colon_in_metric_name_is_parsed(idx):
    # Every vLLM metric is namespaced with a colon; a name regex missing ':'
    # parses this whole file into zero vLLM samples and everything reads None.
    assert any(":" in name for name in idx)
    assert "vllm:num_requests_running" in idx


def test_labels_are_parsed(idx):
    sample = idx["vllm:generation_tokens_total"][0]
    assert sample.labels == {"engine": "0", "model_name": "deepseek-v4-flash-dspark"}


def test_help_type_and_blank_lines_are_skipped(idx):
    # The capture is mostly comments; none of them may become samples.
    assert not any(name.startswith("#") for name in idx)
    # ``http_requests_total`` is declared via # HELP/# TYPE but never emitted.
    assert "http_requests_total" not in idx
    assert sum_by(idx, "http_requests_total") is None


# --------------------------------------------------------------------------
# THE honesty invariant
# --------------------------------------------------------------------------


def test_absent_metric_is_none_and_present_zero_is_zero(idx):
    """The invariant the whole fleet feature rests on.

    A metric we could not read must be ``None``. A metric the server reported as
    ``0`` must be ``0.0``. Collapsing the first into the second would paint an
    unreachable node as a calm, idle, green one.
    """
    # Present in the real capture, genuinely zero.
    assert sum_by(idx, "vllm:num_requests_running") == 0.0
    assert first(idx, "vllm:kv_cache_usage_perc") == 0.0
    # Absent from the payload entirely.
    assert sum_by(idx, "vllm:no_such_metric") is None
    assert first(idx, "vllm:no_such_metric") is None
    # And the two are distinguishable, which `or 0` style code destroys.
    assert sum_by(idx, "vllm:num_requests_running") is not None
    assert sum_by(idx, "vllm:no_such_metric") != 0.0


def test_present_zero_survives_a_label_filter(idx):
    hit = sum_by(
        idx, "vllm:num_requests_running", model_name="deepseek-v4-flash-dspark"
    )
    assert hit == 0.0
    # A label that matches nothing is "unreadable", not zero.
    assert sum_by(idx, "vllm:num_requests_running", model_name="nope") is None


# --------------------------------------------------------------------------
# Parser rules
# --------------------------------------------------------------------------


def test_trailing_timestamp_column_is_tolerated():
    samples = parse_text("metric_with_ts 12.5 1784377459000\nplain_metric 3\n")
    assert samples == [
        Sample("metric_with_ts", {}, 12.5),
        Sample("plain_metric", {}, 3.0),
    ]


def test_escaped_quotes_in_label_values():
    body = r'thing{path="C:\\tmp",note="he said \"hi\"",n="a"} 7' + "\n"
    (sample,) = parse_text(body)
    assert sample.labels == {"path": "C:\\tmp", "note": 'he said "hi"', "n": "a"}
    assert sample.value == 7.0


def test_nan_and_inf_are_dropped_never_zero():
    body = "good_metric 4.0\nnan_metric NaN\npos_inf_metric +Inf\nneg_inf_metric -Inf\n"
    idx = index(parse_text(body))
    assert [s.name for s in parse_text(body)] == ["good_metric"]
    # Unrepresentable is not a measurement: absent, not 0.0.
    for name in ("nan_metric", "pos_inf_metric", "neg_inf_metric"):
        assert sum_by(idx, name) is None
        assert first(idx, name) is None
    assert sum_by(idx, "good_metric") == 4.0


def test_garbage_lines_are_skipped_not_guessed():
    samples = parse_text("this is not exposition\n\n   \nreal_metric 1\n")
    assert samples == [Sample("real_metric", {}, 1.0)]


def test_total_suffix_tolerance_both_directions(idx):
    # Fixture has the _total spelling; a caller asking without it still finds it.
    assert sum_by(idx, "vllm:generation_tokens") == pytest.approx(1763.0)
    # And the reverse: a server exporting the bare name answers a _total lookup.
    bare = index(parse_text("vllm:generation_tokens 42.0\n"))
    assert sum_by(bare, "vllm:generation_tokens_total") == 42.0
    assert first(bare, "vllm:generation_tokens_total") == 42.0


def test_exact_match_wins_over_suffix_tolerance():
    body = "widgets 1.0\nwidgets_total 99.0\n"
    idx = index(parse_text(body))
    assert sum_by(idx, "widgets") == 1.0
    assert sum_by(idx, "widgets_total") == 99.0


def test_suffix_tolerance_does_not_invent_a_match(idx):
    assert sum_by(idx, "vllm:nothing_like_this_total") is None
    assert sum_by(idx, "vllm:nothing_like_this") is None


# --------------------------------------------------------------------------
# Aggregation across label sets
# --------------------------------------------------------------------------


def test_sum_by_aggregates_across_label_sets(idx):
    # Five finished_reason label sets in the real capture: 17 + 5 + 0 + 0 + 0.
    assert len(idx["vllm:request_success_total"]) == 5
    assert sum_by(idx, "vllm:request_success_total") == 22.0
    assert sum_by(idx, "vllm:request_success_total", finished_reason="stop") == 17.0
    assert sum_by(idx, "vllm:request_success_total", finished_reason="length") == 5.0
    assert sum_by(idx, "vllm:request_success_total", finished_reason="abort") == 0.0


def test_sum_by_never_reports_one_models_numbers_as_the_whole():
    """A multi-model server must not have one model mistaken for the node."""
    body = (
        'vllm:generation_tokens_total{model_name="a"} 100.0\n'
        'vllm:generation_tokens_total{model_name="b"} 250.0\n'
    )
    idx = index(parse_text(body))
    assert sum_by(idx, "vllm:generation_tokens_total") == 350.0
    assert first(idx, "vllm:generation_tokens_total") == 100.0  # first, not total
    assert sum_by(idx, "vllm:generation_tokens_total", model_name="b") == 250.0


def test_first_returns_the_first_match(idx):
    per_pos = "vllm:spec_decode_num_accepted_tokens_per_pos_total"
    assert first(idx, per_pos) == 508.0  # position="0", the first line
    assert first(idx, per_pos, position="2") == 287.0
    assert sum_by(idx, per_pos) == pytest.approx(508.0 + 369.0 + 287.0)


def test_label_map(idx):
    reasons = label_map(idx, "vllm:request_success_total", "finished_reason")
    assert reasons == {
        "stop": 17.0,
        "length": 5.0,
        "abort": 0.0,
        "error": 0.0,
        "repetition": 0.0,
    }
    sources = label_map(idx, "vllm:prompt_tokens_by_source_total", "source")
    assert sources["local_cache_hit"] == 91392.0
    assert sources["external_kv_transfer"] == 0.0


def test_label_map_sums_duplicate_keys_rather_than_dropping_them():
    # Two label dimensions collapsing onto one key must not lose a sample.
    body = (
        'calls{mode="streaming",outcome="ok"} 9.0\n'
        'calls{mode="non_streaming",outcome="ok"} 21.0\n'
    )
    idx = index(parse_text(body))
    assert label_map(idx, "calls", "outcome") == {"ok": 30.0}
    # A label absent from the sample is skipped, not keyed as "".
    assert label_map(idx, "calls", "missing_label") == {}


def test_label_map_on_absent_metric_is_empty(idx):
    assert label_map(idx, "vllm:no_such_metric", "model_name") == {}


def test_module_exports_the_contract():
    for name in ("Sample", "parse_text", "index", "first", "sum_by", "label_map"):
        assert hasattr(prometheus, name)
