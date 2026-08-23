"""OutcomeLedger + deterministic downgrade-only tuning (Wave C4, v1.203.0).

Ported from the user's own IronCore project (``ironcore/envelope/outcomes.py``,
its MS-8 self-improvement loop), adapted to Iron Jarvis. The probe battery
measures a model once; live sessions keep producing evidence forever. This
module persists that evidence per (provider, model) and its tuner can
CONSERVATIVELY fold it back into the capability profile:

WHAT IS ACTUALLY FED TODAY, AND WHY THE TUNER IS NOT WIRED (be precise here
— the Wave-C reviewer caught the gap between this file's original thesis and
its feeders, 2026-08-23):

* The production feeders — ``decompose.execute_plan``'s per-step recording
  and ``runtime.step_outcome_recorder`` — send STEP outcomes: the FINAL
  post-retry verdict of each attempted plan step. That is GOAL evidence
  (a verify judge failing on a hard task, an "unverified" pass, a budget
  stop), not protocol evidence (did a tool call parse/validate at the
  active rung), which is what IronCore's tuner consumed and what its
  constants below were calibrated against.
* Consequently :func:`apply_tuning` is DELIBERATELY UNCONSULTED in
  production this wave: importable, fully tested, wired nowhere. Feeding
  goal outcomes through thresholds calibrated for protocol outcomes would
  create a difficulty-driven downgrade spiral — hand a capable model hard
  tasks, watch its rung scores sink, decompose harder, fail differently,
  sink further (the reviewer's finding, 2026-08-23).
* The wiring condition, recorded so the next wave does not guess: either a
  parse/validate evidence feed (the guided strict_json rung is the natural
  source — every reply is mechanically parseable-or-not) or constants
  re-calibrated for goal evidence. Until one exists, recorders keep
  collecting and the ledger keeps its generation bookkeeping, so the day
  the tuner is wired it starts with honest, correctly-stamped history.

The tuner's rules, ported intact for that day:

* **Downgrade-only.** :func:`apply_tuning` may only LOWER a ladder score that
  live evidence contradicts. It never raises one: an upgrade needs a real
  measurement, so a suspiciously clean live rate emits a "re-probe" *hint*,
  nothing more.
* **The frozen ladder stays the sole selector.** Tuning edits the *scores*
  that ``select_tool_protocol`` reads; it never picks a rung itself. A
  lowered score makes the mechanical ladder fall to the next rung by itself.
* **Hysteresis** (IronCore's exact numbers): below :data:`MIN_TOOL_SAMPLES`
  attempts the tuner never acts; counters halve once attempts pass
  :data:`DECAY_CAP` so old evidence fades and files stay bounded; a live rate
  at or above :data:`REPROBE_RATE` earns a hint, never an automatic upgrade.
* **Generation-stamped.** ``ensure_stamp`` resets all counters whenever the
  profile generation changes — a fresh probe (new ``probed_at``), a seed
  replacing floor defaults, or a ``probe_generation`` bump (the Wave-C rung
  semantics change): stale evidence must never re-downgrade a freshly
  measured profile. ``generation_stamp`` treats a tuned overlay as its
  measured base, so tuning itself never resets the evidence it was computed
  from.
* **Corruption-tolerant persistence.** The ledger lives in a
  ``<provider>__<model>.outcomes.json`` sidecar next to the envelope JSON
  (same directory, same sanitized naming, same atomic write); a missing or
  corrupt sidecar loads as a fresh ledger — reads never raise. The tuned
  overlay is recomputed by callers at consult time and never written back to
  the envelope JSON (the cached profile stays the honest measurement).

Adapted, not copied — the deltas from IronCore, recorded so nobody re-files
them as omissions:

* **No edit-format counters** — Iron Jarvis edits through structured tools,
  not diff negotiation (the same reason profile.py has no edit ladder).
* **No turn/drift or verify counters** — no loop records a drift signal this
  wave, and an evidence channel nothing feeds would be dead weight; the
  coherence-horizon tuning that consumed it stays unported until it exists.
* **Both IJ rungs are tunable.** IronCore's ``_tune_ladder`` skipped its last
  rung because that rung (the IRONCALL text floor) was the unconditional
  bottom; Iron Jarvis's floor is ``"none"`` BELOW the ladder, so native AND
  strict_json carry real thresholds and both tune.
* **Keyed (provider, model)** like everything in this package — the same
  model id behind two endpoints is two different deployments.
* **Plain dataclasses** (profile.py's choice), pydantic-free.
* **:func:`record_outcome`** is the one public recording seam: synchronous
  (callers hop through ``asyncio.to_thread`` — the daemon is ONE event loop),
  NEVER raises (evidence is an optimization, not a dependency), and bounded
  (counters halve at the cap; rung keys are pinned to the ladder, so the
  sidecar can never grow past a handful of small counters).

Dependency rules: stdlib + ``envelope.profile`` + ``envelope.store`` only —
nothing here imports the daemon, tools, agents, or providers.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from iron_jarvis.envelope.profile import (
    _MEASURED_SOURCES,
    TOOL_PROTOCOL_LADDER,
    TOOL_PROTOCOL_THRESHOLDS,
    CapabilityProfile,
    sanitize_id,
)
from iron_jarvis.envelope.store import _atomic_write_json, envelope_dir, load_profile

#: Counters halve (attempts AND failures) once attempts exceed this, so the
#: ledger is bounded and old evidence decays instead of pinning a model
#: forever. IronCore's exact number.
DECAY_CAP = 200

#: Hysteresis floor: below this many attempts at a rung the tuner never acts.
#: IronCore's exact number.
MIN_TOOL_SAMPLES = 10

#: A live success rate this clean at the recommended rung earns a re-probe
#: HINT for any higher rung the stored profile keeps below threshold (never
#: an automatic upgrade). IronCore's exact number.
REPROBE_RATE = 0.98


def generation_stamp(profile: CapabilityProfile) -> str:
    """Profile-generation stamp for :meth:`OutcomeLedger.ensure_stamp`.

    Changes exactly when the profile's measurement generation changes — a new
    probe timestamp, a seed replacing floor defaults, or a
    ``probe_generation`` bump (the Wave-C addition: when the rung semantics
    change under stored scores, evidence collected against the old semantics
    is void too) — and is INVARIANT under tuning: a ``"tuned"`` overlay
    carries its measured base's stamp (``tuned`` preserves ``probed_at`` and
    ``probe_generation``), otherwise consult-time tuning would reset the very
    evidence it was computed from.
    """
    if profile.source in _MEASURED_SOURCES or profile.probed_at is not None:
        return f"gen{profile.probe_generation}:measured:{profile.probed_at or ''}"
    return f"gen{profile.probe_generation}:{profile.source}:"


@dataclass
class Counter:
    """Attempt/failure tally for one ladder rung, with halving decay."""

    attempts: int = 0
    failures: int = 0

    def record(self, ok: bool) -> None:
        self.attempts += 1
        if not ok:
            self.failures += 1
        if self.attempts > DECAY_CAP:
            self.attempts //= 2
            self.failures //= 2

    def success_rate(self) -> float:
        """Successes / attempts; NO evidence reads as 1.0 (absence of evidence
        must never look like failure — the tuner also gates on min samples)."""
        if self.attempts <= 0:
            return 1.0
        return 1.0 - (self.failures / self.attempts)


def _clean_counters(raw: Any) -> dict[str, Counter]:
    """Counters out of a JSON payload, dropping anything malformed and any
    rung outside the ladder — the tuner can only consume ladder rungs, and
    pinning the keys is half of the sidecar's size bound."""
    counters: dict[str, Counter] = {}
    if not isinstance(raw, dict):
        return counters
    for rung, value in raw.items():
        if rung not in TOOL_PROTOCOL_LADDER or not isinstance(value, dict):
            continue
        attempts, failures = value.get("attempts"), value.get("failures")
        if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
            continue
        if not isinstance(failures, int) or isinstance(failures, bool) or failures < 0:
            continue
        counters[rung] = Counter(attempts=attempts, failures=min(failures, attempts))
    return counters


@dataclass
class OutcomeLedger:
    """Per-(provider, model) live-session evidence, persisted as a sidecar
    next to the envelope JSON. Recording methods are cheap and never raise;
    loads treat a bad sidecar as a fresh ledger, never a crash."""

    model_id: str
    provider: str = ""
    version: int = 1
    #: generation stamp of the profile the counters were collected against.
    profile_stamp: str = ""
    tool_protocols: dict[str, Counter] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    def record_tool_attempt(self, protocol: str, ok: bool) -> None:
        """One tool-call outcome at ``protocol``. Rungs outside the ladder
        are refused silently — they could never be consumed by the tuner and
        an open key set would break the sidecar's size bound."""
        if protocol not in TOOL_PROTOCOL_LADDER:
            return
        self.tool_protocols.setdefault(protocol, Counter()).record(bool(ok))

    def ensure_stamp(self, stamp: str) -> bool:
        """Reset every counter when the profile generation changed.

        Called with ``generation_stamp(active_profile)`` before recording or
        tuning: evidence collected against an old generation — a pre-probe
        floor, a prior probe, or a prior ``probe_generation`` — must not
        re-downgrade a freshly measured profile. Returns True on a reset.
        """
        if stamp == self.profile_stamp:
            return False
        self.profile_stamp = stamp
        self.tool_protocols.clear()
        return True

    # ------------------------------------------------------------------ #
    # Persistence (sidecar next to the profile store, same atomicity)
    # ------------------------------------------------------------------ #

    @staticmethod
    def path_for(home: Path, provider: str, model_id: str) -> Path:
        """``<home>/envelopes/<provider>__<model>.outcomes.json`` — the same
        sanitized naming as the profile it sits beside."""
        name = f"{sanitize_id(provider)}__{sanitize_id(model_id)}.outcomes.json"
        return envelope_dir(home) / name

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> OutcomeLedger:
        """Build from a JSON payload; malformed counters are dropped, never
        trusted. Raises only on payloads that are not ledger-shaped at all —
        :meth:`load` catches that and answers a fresh ledger."""
        return cls(
            model_id=str(data.get("model_id") or ""),
            provider=str(data.get("provider") or ""),
            version=1,
            profile_stamp=str(data.get("profile_stamp") or ""),
            tool_protocols=_clean_counters(data.get("tool_protocols")),
        )

    def save(self, home: Path) -> Path:
        """Write the sidecar atomically (the profile store's staged write —
        a crash mid-write leaves the PREVIOUS ledger intact). May raise
        ``OSError``; callers treating persistence as best-effort catch it
        (:func:`record_outcome` does)."""
        path = self.path_for(home, self.provider, self.model_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, self.to_dict())
        return path

    @classmethod
    def load(cls, home: Path, provider: str, model_id: str) -> OutcomeLedger:
        """The (provider, model) ledger; missing/corrupt/mismatched -> a
        FRESH ledger. Never raises — evidence is an optimization, not a
        dependency."""
        path = cls.path_for(home, provider, model_id)
        ledger: OutcomeLedger | None = None
        try:
            data = json.loads(path.read_bytes())
            if isinstance(data, dict):
                ledger = cls.from_dict(data)
        except (OSError, ValueError, TypeError):
            ledger = None
        if ledger is None or ledger.model_id != model_id or ledger.provider != provider:
            ledger = cls(model_id=model_id, provider=provider)
        return ledger


# --------------------------------------------------------------------------- #
# The one public recording seam (the runtime's verify/retry ladder calls this)
# --------------------------------------------------------------------------- #


def record_outcome(
    home: Path, provider: str, model_id: str, ok: bool, *, protocol: str | None = None
) -> None:
    """Record one live outcome for ``provider``/``model_id``.

    What ``ok`` MEANS is whatever the caller feeds — and today's production
    feeders (``decompose.execute_plan`` per-step recording via
    ``runtime.step_outcome_recorder``) feed STEP outcomes: the final
    post-retry verdict of an attempted plan step, so ``ok=False`` includes a
    judge failing a HARD task and a budget stop, not only a malformed tool
    call. That is goal evidence, and it is exactly why :func:`apply_tuning`
    stays unconsulted in production this wave (module docstring, reviewer
    finding 2026-08-23): the thresholds here were calibrated for
    protocol-adherence evidence. Recording continues anyway — the ledger's
    generation stamping keeps the history honest for the wave that wires a
    parse/validate feed or re-calibrates.

    NEVER raises, by contract — this is called from the agent loop's hot path
    and a bookkeeping fault must not fail a turn. Synchronous file I/O, so
    async callers hop through ``asyncio.to_thread`` (v1.153.1). Bounded: one
    small JSON sidecar whose counters halve at :data:`DECAY_CAP` and whose
    keys are pinned to the ladder.

    Attribution: the outcome lands on ``protocol`` when the caller names the
    rung it actually drove, else on the stored profile's own
    ``select_tool_protocol()``. No stored MEASURED profile (trusted, seeded,
    floor, probe_failed) or no active rung (``"none"``) -> recorded nowhere:
    there is no rung to attribute the evidence to and the tuner could never
    act on it (it only tunes measured profiles).
    """
    try:
        profile = load_profile(home, provider, model_id)
        if profile is None or profile.is_trusted() or not profile.is_measured():
            return
        rung = protocol if protocol is not None else profile.select_tool_protocol()
        if rung not in TOOL_PROTOCOL_LADDER:
            return
        ledger = OutcomeLedger.load(home, provider, model_id)
        ledger.ensure_stamp(generation_stamp(profile))
        ledger.record_tool_attempt(rung, ok)
        ledger.save(home)
    except Exception:  # noqa: BLE001 — never-raising by contract
        return


# --------------------------------------------------------------------------- #
# The deterministic tuner
# --------------------------------------------------------------------------- #


@dataclass
class TuningResult:
    """What :func:`apply_tuning` decided: the (possibly adjusted) profile
    copy, human-readable adjustment notes, and upgrade hints (never applied)."""

    profile: CapabilityProfile
    adjustments: list[str] = field(default_factory=list)
    reprobe_hints: list[str] = field(default_factory=list)


def _tune_ladder(
    scores: dict[str, float],
    counters: dict[str, Counter],
    min_samples: int,
    adjustments: list[str],
) -> None:
    """Lower any above-threshold stored score whose live rate falls below the
    FROZEN threshold — the mechanical ladder then falls to the next rung on
    its own. ``min(stored, live)`` can only ever lower. Every IJ rung tunes
    (the floor ``"none"`` sits below the ladder — see the module docstring)."""
    for rung in TOOL_PROTOCOL_LADDER:
        counter = counters.get(rung)
        if counter is None or counter.attempts < min_samples:
            continue
        live = counter.success_rate()
        threshold = TOOL_PROTOCOL_THRESHOLDS[rung]
        stored = scores.get(rung, 0.0)
        if live < threshold and stored >= threshold:
            scores[rung] = min(stored, live)
            adjustments.append(
                f"tool protocol {rung}: stored {stored:.2f} but live success "
                f"{live:.2f} over {counter.attempts} attempts (needs >= "
                f"{threshold:.2f}) - lowered to {scores[rung]:.2f}"
            )


def _ladder_hints(
    scores: dict[str, float],
    counters: dict[str, Counter],
    recommended: str,
    min_samples: int,
) -> list[str]:
    """Upgrade HINTS only: a clean live rate at the recommended rung, with a
    higher rung stored below threshold, asks for a re-measure — it never
    edits the profile (upgrades require a real probe). ``recommended`` may be
    ``"none"`` (below the ladder): no rung is producing evidence then."""
    if recommended not in TOOL_PROTOCOL_LADDER:
        return []
    counter = counters.get(recommended)
    if counter is None or counter.attempts < min_samples:
        return []
    live = counter.success_rate()
    if live < REPROBE_RATE:
        return []
    hints: list[str] = []
    ladder = list(TOOL_PROTOCOL_LADDER)
    for rung in ladder[: ladder.index(recommended)]:
        if scores.get(rung, 0.0) < TOOL_PROTOCOL_THRESHOLDS[rung]:
            hints.append(
                f"tool protocol {recommended} is succeeding ({live:.2f} over "
                f"{counter.attempts} attempts) - re-probe to re-measure {rung} "
                "(upgrades are never applied automatically)"
            )
    return hints


def apply_tuning(
    profile: CapabilityProfile,
    ledger: OutcomeLedger,
    *,
    min_tool_samples: int = MIN_TOOL_SAMPLES,
) -> TuningResult:
    """Fold live evidence into a COPY of ``profile`` (input never mutated).

    Applies only when the evidence is trustworthy: the profile is MEASURED
    (tuning floor defaults is meaningless — they are already the bottom — and
    a trusted grant is not evidence to contradict), the ledger belongs to
    this provider+model, and its generation stamp matches (evidence against
    another generation is void; ``ensure_stamp`` will reset it — the Wave-C
    rule: a NEW ``probe_generation`` resets the ledger's evidence). If
    anything was adjusted the copy is marked ``source="tuned"`` with
    ``probed_at`` (and ``probe_generation``) preserved: the base measurement
    stands, live evidence only lowered it.
    """
    tuned = profile.copy()
    if (
        profile.is_trusted()
        or not profile.is_measured()
        or ledger.model_id != profile.model_id
        or ledger.provider != profile.provider
        or ledger.profile_stamp != generation_stamp(profile)
    ):
        return TuningResult(profile=tuned)

    adjustments: list[str] = []
    _tune_ladder(tuned.tool_protocols, ledger.tool_protocols, min_tool_samples, adjustments)
    hints = _ladder_hints(
        tuned.tool_protocols,
        ledger.tool_protocols,
        tuned.select_tool_protocol(),
        min_tool_samples,
    )
    if adjustments:
        tuned.source = "tuned"  # probed_at + probe_generation preserved by copy()
    return TuningResult(profile=tuned, adjustments=adjustments, reprobe_hints=hints)
