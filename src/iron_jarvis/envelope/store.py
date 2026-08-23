"""Envelope store: atomic profile persistence + the keep-last-good merge.

Profiles live under ``<home>/envelopes/<provider>__<model>.json`` (both ids
sanitized — model ids carry ``/`` and ``:``). Two rules ported from IronCore,
each learned the expensive way there:

* **Writes are ATOMIC** (stage under a unique name in the target's own
  directory, fsync, ``os.replace``). The first-run probe is the one write a
  fresh install performs unasked, and quitting during it must never leave a
  half-written file at the live path — that used to brick IronCore's boot.
* **Loads NEVER raise.** A missing cache and a corrupt cache both read as
  "unprobed" (``None``); the corrupt file is quarantined best-effort to
  ``.corrupt`` so the evidence stays inspectable and the live path is freed.

The third rule is the merge, :func:`merge_keep_last_good` — the refined
IC-1214 behavior from IronCore's ``runner.durable_profile``: **a failed
measurement never destroys a good one in the RECORD.** A ``probe_failed``
battery carries the cached record forward WHOLESALE (restamped with the
failure, no ``probed_at``); a battery that measured SOMETHING inherits from
the record every non-reliability field it was supposed to measure and did not
deliver, while the dead probes' reliabilities stay floored at 0.0 (a
preserved 0.98 would sit beside numbers this run really measured with nothing
telling them apart).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path

from iron_jarvis.envelope.profile import CapabilityProfile, sanitize_id

#: Dotted-path taxonomy for the merge (the quick battery's targets).
#: A path rooted here is a RELIABILITY: it degrades to 0.0 when its probe
#: dies and is deliberately NOT restored from the record on a partial run.
_RELIABILITY_ROOTS = frozenset({"tool_protocols", "json_adherence"})
#: Non-reliability scalars a probe can measure; a dead probe leaves them at
#: the base and the SAVED record inherits them from the cached record.
_INT_FIELDS = frozenset({"honest_context", "context_window", "coherence_horizon"})
_FLOAT_FIELDS = frozenset({"json_adherence", "chars_per_token"})
_DICT_FIELDS = frozenset({"tool_protocols"})

#: A staging file older than this is nobody's live write, so it is safe to
#: reap. Generous on purpose: a concurrent writer's in-flight staging file
#: must never be swept out from under it.
_STAGING_STALE_SECONDS = 3600.0


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


def envelope_dir(home: Path) -> Path:
    return Path(home) / "envelopes"


def profile_path(home: Path, provider: str, model_id: str) -> Path:
    """``<home>/envelopes/<provider>__<model>.json``, both halves sanitized so
    ``qwen3:30b/instruct`` becomes one flat filename, never a subpath."""
    name = f"{sanitize_id(provider)}__{sanitize_id(model_id)}.json"
    return envelope_dir(home) / name


# --------------------------------------------------------------------------- #
# Atomic write (ported from IronCore's _atomic_write_json + staging sweep)
# --------------------------------------------------------------------------- #


def _sweep_stale_staging(path: Path) -> None:
    """Reap abandoned staging files for ``path``. The except-OSError below
    cleans up a failed write, but an interruption that is not an OSError — a
    KeyboardInterrupt during the first-ever probe — unwinds past it and
    strands a ``.<name>.xxxx.tmp``. Best-effort: a sweep failure must never
    fail the save it precedes."""
    cutoff = time.time() - _STAGING_STALE_SECONDS
    try:
        stale = list(path.parent.glob(f".{path.name}.*.tmp"))
    except OSError:
        return
    for leftover in stale:
        try:
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()
        except OSError:  # locked, or another writer got there first
            continue


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Stage under a UNIQUE name in the target's own directory (same volume,
    so ``os.replace`` is atomic), fsync, then rename over the target. The
    staging name is unique per writer, not ``<target>.tmp``: two daemons
    probing the same model share this directory with no lock, and a shared
    staging name lets writer A publish writer B's half-written bytes."""
    _sweep_stale_staging(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def save_profile(home: Path, profile: CapabilityProfile) -> Path:
    """Write ``profile`` atomically at its canonical path and return it."""
    path = profile_path(home, profile.provider, profile.model_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, profile.to_dict())
    return path


# --------------------------------------------------------------------------- #
# Never-raising load
# --------------------------------------------------------------------------- #


def load_profile(home: Path, provider: str, model_id: str) -> CapabilityProfile | None:
    """The cached profile, or ``None`` when there isn't a usable one.

    NEVER raises: a missing cache and a corrupt one both read as "unprobed".
    Bytes, not text — ``json.loads`` does its own decoding, so a payload that
    is not valid UTF-8 (power-loss garbage, an AV-quarantine stub) raises
    inside the guard instead of bricking every boot via ``read_text``.
    """
    path = profile_path(home, provider, model_id)
    try:
        raw = path.read_bytes()
    except OSError:  # missing = the normal first boot; unreadable = same outcome
        return None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("profile payload is not an object")
        return CapabilityProfile.from_dict(data)
    except (ValueError, TypeError):
        pass
    # Quarantine rather than delete: the evidence stays inspectable, and the
    # live path is freed so the next probe can write a good cache. Best-effort
    # — a locked file still answers None.
    try:
        os.replace(path, path.with_name(path.name + ".corrupt"))
    except OSError:
        pass
    return None


# --------------------------------------------------------------------------- #
# Keep-last-good (the IC-1214 merge, adapted to the quick battery)
# --------------------------------------------------------------------------- #


def _empty_record(provider: str, model_id: str) -> CapabilityProfile:
    """The record a first-ever total failure writes: the floor, restamped
    with the failure. One expression, read back by identity in
    :func:`_holds_no_evidence` — the anchor is what makes a first-ever
    failure distinguishable from a preserved measurement."""
    return CapabilityProfile(model_id=model_id, provider=provider, source="probe_failed")


def _holds_no_evidence(record: CapabilityProfile) -> bool:
    """Must the merge refuse to restore out of ``record``? A NARROW question:
    only the artifact this module writes to MEAN nothing (the first-ever
    failure floor), plus ``seeded`` — a seed is the endpoint's claim about
    itself, never cached by the seeder, and restoring out of one here would
    be a back door around that rule."""
    return record.source == "seeded" or record == _empty_record(record.provider, record.model_id)


def _value_at(profile: CapabilityProfile, path: str) -> float | None:
    """The value ``profile`` holds at one dotted path, or ``None`` when it
    holds nothing there. Exact inverse of :func:`merge_value` so the two
    cannot disagree about which paths exist."""
    head, _, tail = path.partition(".")
    if head in _DICT_FIELDS and tail:
        value = getattr(profile, head).get(tail)
        return None if value is None else float(value)
    if head in _INT_FIELDS and not tail:
        return float(getattr(profile, head))
    if head in _FLOAT_FIELDS and not tail:
        return float(getattr(profile, head))
    return None


def merge_value(profile: CapabilityProfile, path: str, value: float) -> None:
    """Write one dotted-path value into ``profile`` in place. Unknown or
    malformed paths are skipped, never fatal — a probe-author bug must not
    abort a save. ``int()`` TRUNCATES deliberately (the conservative
    direction for context numbers)."""
    head, _, tail = path.partition(".")
    if head in _DICT_FIELDS and tail:
        getattr(profile, head)[tail] = float(value)
    elif head in _INT_FIELDS and not tail:
        setattr(profile, head, int(value))
    elif head in _FLOAT_FIELDS and not tail:
        setattr(profile, head, float(value))


def is_reliability_path(path: str) -> bool:
    return path.partition(".")[0] in _RELIABILITY_ROOTS


def merge_keep_last_good(
    measured: CapabilityProfile,
    record: CapabilityProfile | None,
    *,
    unverified: Iterable[str] = (),
) -> CapabilityProfile:
    """The profile to WRITE, given what this run produced and what is cached.

    Ported from IronCore ``runner.durable_profile`` (IC-1214), simplified to
    the quick battery's field set. The runner floors a failed probe's
    reliabilities to 0.0 — right for the SESSION (never drive a rung nothing
    verified), wrong for the RECORD: a re-probe against a temporarily dead
    endpoint must not overwrite a real ``native 0.98`` with a 0.0 nothing
    measured. Three branches:

    * ``probe_failed`` — the record is carried WHOLESALE (not spliced: a real
      ``native 0.98`` beside an honest_context this run never measured either
      is two measurements welded into one profile), restamped
      ``source="probe_failed"`` / ``probed_at=None``. Both halves matter: the
      carry stops the data loss; the restamp is what makes the app re-probe
      instead of trusting a battery that never landed. With NO record the
      anchor is the FLOOR, never this run's own profile — a seed the battery
      failed to refine must not write the endpoint's introspection into the
      cache.
    * ``probed``/``partial`` over a record holding evidence — every
      ``unverified`` path (declared by a probe, not delivered by this run —
      which covers TOKEN-RATIO's designed-for ``ok=True`` with empty scores
      when the server omits usage) that is NOT a reliability is restored from
      the record. Reliabilities stay floored: a partial card prints its rung
      scores, and a preserved number would masquerade as this run's evidence.
    * anything else (hand-built source, no record, or a record that holds no
      evidence) — this run's profile stands as-is.

    ``measured`` is returned by identity on the no-merge branches and copied
    when a merge happens; ``record`` is never aliased into the result.

    ``measured_fields`` flows with the values it describes, branch by branch:
    the WHOLESALE carry keeps the record's list (the values survived, so does
    their provenance) — and the first-ever-failure floor anchor keeps the
    floor's EMPTY list, since nothing there is evidence; the partial/probed
    merge first DROPS every unverified path from this run's list (this run
    did not deliver it, whatever a hand-built caller claimed), then re-adds a
    restored path only when the RECORD held it as measured — a legacy record
    written before per-field provenance restores its value but honestly
    under-claims it as unmeasured.
    """
    if measured.source == "probe_failed":
        anchor = record if record is not None else _empty_record(measured.provider, measured.model_id)
        durable = anchor.copy()
        # the run's own identity, so the file lands under the model that was
        # probed even if the cached record spelled it differently.
        durable.model_id = measured.model_id
        durable.provider = measured.provider
        durable.source = "probe_failed"
        durable.probed_at = None
        return durable
    if (
        record is None
        or measured.source not in ("probed", "partial")
        or _holds_no_evidence(record)
    ):
        return measured
    durable = measured.copy()
    provenance = set(durable.measured_fields)
    for path in sorted(set(unverified)):
        provenance.discard(path)  # declared, not delivered — never this run's evidence
        if is_reliability_path(path):
            continue  # stays floored: the card would print it as a score
        value = _value_at(record, path)
        if value is not None:
            merge_value(durable, path, value)
            if path in record.measured_fields:
                provenance.add(path)  # restored evidence keeps its provenance
    durable.measured_fields = sorted(provenance)
    return durable


def save_measurement(
    home: Path,
    measured: CapabilityProfile,
    *,
    unverified: Iterable[str] = (),
) -> CapabilityProfile:
    """Persist a battery's outcome under keep-last-good rules and return the
    DURABLE profile that was written (the caller usually keeps driving the
    session on ``measured`` — a session that just watched every probe die
    should run the floor, whatever the record still says)."""
    record = load_profile(home, measured.provider, measured.model_id)
    durable = merge_keep_last_good(measured, record, unverified=unverified)
    save_profile(home, durable)
    return durable
