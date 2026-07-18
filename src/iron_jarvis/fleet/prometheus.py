"""A small, dependency-free Prometheus text-exposition parser.

``prometheus_client`` is not a dependency of this project and must not become
one for a read-only scrape parser — this is ~100 lines of ``re``.

The rule the whole fleet feature rests on lives in this file: **absent and zero
are different**. A metric missing from the payload makes :func:`first`/
:func:`sum_by` return ``None``; a metric present with value ``0`` returns
``0.0``. The user's own vLLM box reports ``vllm:num_requests_running 0.0`` while
it is idle, so if we ever collapsed "absent" to 0 an unreachable server would be
indistinguishable from a healthy idle one. Same reason ``NaN``/``+Inf``/``-Inf``
are dropped rather than coerced: an unrepresentable value is not a measurement.
"""

from __future__ import annotations

import math
import re
from typing import NamedTuple

# Metric names may contain a colon — vLLM namespaces every metric that way
# (``vllm:num_requests_running``), so a name pattern without ``:`` silently
# parses zero vLLM samples. The trailing group tolerates the optional timestamp
# column of the exposition format, which we do not use.
_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?"
    r"[ \t]+(?P<value>\S+)"
    r"(?:[ \t]+\S+)?[ \t]*$"
)
# Label values are quoted and may contain escaped quotes/backslashes.
_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:[^"\\]|\\.)*)"')
_UNESCAPE = {'\\"': '"', "\\\\": "\\", "\\n": "\n"}


class Sample(NamedTuple):
    """One parsed metric line."""

    name: str
    labels: dict[str, str]
    value: float


def _unescape(raw: str) -> str:
    return re.sub(r'\\[\\"n]', lambda m: _UNESCAPE[m.group(0)], raw)


def _parse_value(raw: str) -> float | None:
    """``None`` for anything that is not a finite number.

    ``NaN``/``+Inf``/``-Inf`` are legal exposition values but not measurements,
    so they are dropped entirely rather than becoming a misleading ``0.0``.
    """
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def parse_text(body: str) -> list[Sample]:
    """Parse an exposition payload. Unparseable lines are skipped, not guessed."""
    out: list[Sample] = []
    for line in (body or "").splitlines():
        line = line.strip()
        # Blank lines and every comment form (# HELP / # TYPE / # anything).
        if not line or line.startswith("#"):
            continue
        m = _SAMPLE_RE.match(line)
        if not m:
            continue
        value = _parse_value(m.group("value"))
        if value is None:
            continue
        labels = {
            k: _unescape(v) for k, v in _LABEL_RE.findall(m.group("labels") or "")
        }
        out.append(Sample(m.group("name"), labels, value))
    return out


def index(samples: list[Sample]) -> dict[str, list[Sample]]:
    """Group samples by metric name (one name, many label sets)."""
    idx: dict[str, list[Sample]] = {}
    for s in samples:
        idx.setdefault(s.name, []).append(s)
    return idx


def _lookup(idx: dict[str, list[Sample]], name: str) -> list[Sample]:
    """Exact match, then ``_total`` tolerance in BOTH directions.

    Callers should not have to know whether a given server's build spells a
    counter ``vllm:generation_tokens`` or ``vllm:generation_tokens_total`` — the
    suffix has moved between vLLM versions. Exact always wins so a lookup can
    never be hijacked by a differently-suffixed sibling metric.
    """
    if name in idx:
        return idx[name]
    alt = name[: -len("_total")] if name.endswith("_total") else f"{name}_total"
    return idx.get(alt, [])


def _matching(
    idx: dict[str, list[Sample]], name: str, want: dict[str, str]
) -> list[Sample]:
    hits = _lookup(idx, name)
    if not want:
        return hits
    return [s for s in hits if all(s.labels.get(k) == v for k, v in want.items())]


def first(idx: dict[str, list[Sample]], name: str, **want: str) -> float | None:
    """Value of the first matching sample, or ``None`` if there is no match."""
    hits = _matching(idx, name, want)
    return hits[0].value if hits else None


def sum_by(idx: dict[str, list[Sample]], name: str, **want: str) -> float | None:
    """Total across every matching label set — ``None`` when nothing matched.

    Summing (rather than taking the first) matters on a multi-model server: with
    two ``model_name`` label sets, ``first`` would report ONE model's counter as
    if it were the whole node's throughput. ``None`` vs ``0.0`` is preserved:
    no matching sample is "we could not read this", not "it is zero".
    """
    hits = _matching(idx, name, want)
    return math.fsum(s.value for s in hits) if hits else None


def max_by(idx: dict[str, list[Sample]], name: str, **want: str) -> float | None:
    """Largest value across matching label sets — ``None`` when nothing matched.

    The right reducer for a RATIO (``kv_cache_usage_perc`` is 0..1 per engine):
    summing two engines at 0.9 would report 1.8, i.e. an impossible "180% KV
    cache". The max answers the question the number is actually asked for — how
    close is the most-pressured engine to full.
    """
    hits = _matching(idx, name, want)
    return max(s.value for s in hits) if hits else None


def label_map(idx: dict[str, list[Sample]], name: str, label: str) -> dict[str, float]:
    """Map one label's values to their metric values (e.g. per ``model_name``).

    Samples sharing a label value are summed rather than overwriting each other,
    so a second label dimension can never silently discard data.
    """
    out: dict[str, float] = {}
    for s in _lookup(idx, name):
        key = s.labels.get(label)
        if key is None:
            continue
        out[key] = out.get(key, 0.0) + s.value
    return out
