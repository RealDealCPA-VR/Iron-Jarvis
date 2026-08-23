"""Battery runner: orchestrate probes into a CapabilityProfile, honestly.

Ported from IronCore ``envelope/runner.py`` — the provenance rules there were
hardened across many fix rounds and are carried over intact:

* **Partial failure never aborts the run.** A probe that raises, times out,
  or answers ``ok=False`` is converted into a degraded result (its declared
  RELIABILITY targets floored to 0.0, a note recorded); every other probe
  still runs. Non-reliability targets are left at the base — a failed
  measurement must not invent a token ratio or a smaller context.
* **Provenance follows the evidence** (:func:`_battery_provenance`): all
  probes answered -> ``"probed"`` + stamp; some -> ``"partial"`` + stamp
  (something WAS measured); none -> ``"probe_failed"`` and NO ``probed_at`` —
  a stamp alone reads as "measured" on every surface, and dropping it is what
  makes the app re-probe next boot instead of trusting a battery that never
  landed. An EMPTY battery is ``probe_failed`` too: both readings of zero
  probes are vacuous, so the tie breaks by consequence — an empty battery
  produced no evidence, and labeling it ``probed`` once let IronCore replace
  a cached measurement with a seed's introspection under a clean label.
* **A path is measured when a probe DELIVERED it, not when its probe
  survived** (:func:`_unverified_targets` — DECLARED minus DELIVERED).
  ``ok=True`` is the probe's confidence, not coverage: the shipped
  TOKEN-RATIO probe answers ``ok=True`` with EMPTY scores whenever the
  server omits usage (normal for llama.cpp / LM Studio / a LiteLLM proxy),
  and treating that as verified once overwrote a measured ``chars_per_token
  3.6`` with the 4.0 default on IronCore.
* **A failed measurement never destroys a good one in the RECORD.** The
  session profile this function returns stays degraded (a session that just
  watched every probe die should drive the floor); what is SAVED goes
  through the store's keep-last-good merge, so a network blip cannot delete
  a measurement that took a full battery to make.

Everything here is pure async and off-loop-safe: the one disk write goes
through ``asyncio.to_thread`` (the daemon is ONE event loop, and a first
probe must never freeze it).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from iron_jarvis.envelope import store
from iron_jarvis.envelope.probes import ProbeResult, Transport, quick_battery
from iron_jarvis.envelope.profile import CapabilityProfile

#: Whole-battery wall-clock budget in seconds. Generous for three quick
#: probes; what it exists to bound is a live endpoint that accepts the
#: connection and then trickles — without a deadline one wedged server keeps
#: a background probe task alive forever.
DEFAULT_TOTAL_TIMEOUT = 120.0


def _degraded(probe: Any, note: str) -> ProbeResult:
    """The conservative floor for a probe that produced no trustworthy
    result: reliability targets -> 0.0; non-reliability targets are omitted
    so they keep the base value."""
    targets = tuple(getattr(probe, "targets", ()) or ())
    return ProbeResult(
        probe_id=str(getattr(probe, "id", "unknown")),
        scores={t: 0.0 for t in targets if store.is_reliability_path(t)},
        notes=f"{note}; reliabilities degraded to 0.0",
        ok=False,
    )


async def evaluate_probes(
    probes: Sequence[Any],
    transport: Transport,
    *,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
) -> list[ProbeResult]:
    """Run every probe against ``transport``, tolerating partial failure.

    **Exactly one result per probe, in order** — every branch appends before
    it continues, and :func:`_unverified_targets` pairs the two lists
    positionally. The deadline is shared: each probe gets whatever budget
    remains, and a probe reached after the budget is spent is degraded with
    an honest note rather than silently skipped (a skipped probe would break
    the one-result-per-probe pairing AND read as "never asked").
    """
    deadline = time.monotonic() + max(0.0, total_timeout)
    results: list[ProbeResult] = []
    for probe in probes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            results.append(_degraded(probe, "battery time budget exhausted before this probe"))
            continue
        try:
            result = await asyncio.wait_for(probe.run(transport), timeout=remaining)
        except asyncio.TimeoutError:
            results.append(_degraded(probe, f"probe timed out after {total_timeout:.0f}s budget"))
            continue
        except Exception as exc:  # noqa: BLE001 — partial-failure tolerance is the point
            results.append(_degraded(probe, f"probe raised {type(exc).__name__}: {exc}"))
            continue
        if not result.ok:
            # normalize: a self-declared failure floors its targets the same
            # way a raise does, and the note keeps the probe's own words.
            degraded = _degraded(probe, result.notes or "probe reported ok=False")
            degraded.probe_id = result.probe_id
            results.append(degraded)
            continue
        results.append(result)
    return results


def _unverified_targets(
    probes: Sequence[Any], results: Sequence[ProbeResult]
) -> frozenset[str]:
    """Dotted paths a probe in THIS battery declared and did not deliver —
    DECLARED minus DELIVERED, not "the dead probes' targets": a probe that
    answers ``ok=True`` while filling nothing (TOKEN-RATIO on a no-usage
    server, by design) never enters the failed set, and only coverage can
    answer "did this run measure that field?". A path another probe DID
    deliver is verified — the run's own evidence always wins. ``strict=True``
    pins that these two lists describe the same battery."""
    declared: set[str] = set()
    delivered: set[str] = set()
    for probe, result in zip(probes, results, strict=True):
        declared.update(getattr(probe, "targets", ()) or ())
        if result.ok:
            delivered.update(result.scores)
    return frozenset(declared - delivered)


def _battery_provenance(
    results: Sequence[ProbeResult], probed_at: str
) -> tuple[str, str | None]:
    """``(source, probed_at)`` from what the battery actually achieved — see
    the module docstring. ``probe_failed`` NEVER carries a stamp."""
    if not results:
        return "probe_failed", None
    failed = sum(1 for result in results if not result.ok)
    if not failed:
        return "probed", probed_at
    if failed == len(results):
        return "probe_failed", None
    return "partial", probed_at


def _fold_results(
    results: Sequence[ProbeResult],
    *,
    base: CapabilityProfile,
    probed_at: str,
) -> CapabilityProfile:
    """``base`` (copied, never mutated) + these results -> the session
    profile. Provenance is stamped over whatever the base claimed: a refined
    profile inherits its base's FIELDS, never its base's provenance.

    ``measured_fields`` follows the same evidence rule PER FIELD (the minimal
    IC-1215 — the battery-level stamp once laundered a never-measured
    honest_context into "probed" and shrank a 128k window to the 4096 floor):

    * DELIVERED paths (an ok result really carried them) are evidence.
    * paths a DEGRADED result floored to 0.0 are NOT — they overwrite
      whatever the base held, so the base's claim on them is dropped too.
    * base fields this run never touched keep the base's own
      ``measured_fields`` claim: the value survived, and so does its
      provenance (a re-probe whose base is the cached record must not strip
      the record's untouched measurements).
    """
    profile = base.copy()
    delivered: set[str] = set()
    floored: set[str] = set()
    for result in results:
        (delivered if result.ok else floored).update(result.scores)
        for path, value in result.scores.items():
            store.merge_value(profile, path, value)
    kept = set(profile.measured_fields) - floored
    profile.measured_fields = sorted(kept | delivered)
    profile.source, profile.probed_at = _battery_provenance(results, probed_at)
    return profile


async def run_quick_battery(
    profile: CapabilityProfile,
    transport: Transport,
    *,
    probes: Sequence[Any] | None = None,
    home: Path | None = None,
    probed_at: str | None = None,
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT,
) -> CapabilityProfile:
    """Measure the model behind ``transport`` and return the SESSION profile.

    ``profile`` is the base (typically a seed, or the floor) and is refined,
    not replaced: fields no probe measures keep their base value. When
    ``home`` is given the outcome is also persisted under the store's
    keep-last-good rules — **two profiles, deliberately**: what is returned
    stays degraded so a session that watched its probes die drives the floor,
    while what is saved inherits the cached record's measurements for
    everything this battery owed and did not deliver. ``probed_at`` is
    injectable for deterministic tests; the default stamps UTC now.
    """
    battery = list(probes) if probes is not None else quick_battery()
    stamp = probed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = await evaluate_probes(battery, transport, total_timeout=total_timeout)
    session = _fold_results(results, base=profile, probed_at=stamp)
    if home is not None:
        unverified = _unverified_targets(battery, results)
        # one disk write, off the loop — the daemon is ONE asyncio loop and
        # fsync on a slow disk is exactly the freeze v1.153.1 exists to ban.
        await asyncio.to_thread(
            store.save_measurement, home, session, unverified=unverified
        )
    return session
