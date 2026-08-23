"""OutcomeLedger + downgrade-only tuning (Wave C4, v1.203.0). Pure/offline.

The tuner's contract, ported from IronCore's MS-8 suite and adapted to Iron
Jarvis: DOWNGRADE-ONLY (a good ledger never RAISES a score), evidence-gated
(min-sample hysteresis + a matching generation stamp + a measured, untrusted
base), pure (the input profile is never mutated), and always working through
the frozen thresholds + mechanical ladder — it edits scores, never selects
rungs. Persistence mirrors the envelope cache: a sanitized
``<provider>__<model>.outcomes.json`` sidecar, corruption-tolerant, bounded
by halving decay and ladder-pinned keys. The Wave-C rule under test
throughout: a NEW probe generation (a fresh ``probed_at`` OR a
``probe_generation`` bump) voids the ledger's evidence.
"""

from __future__ import annotations

import json

from iron_jarvis.envelope.outcomes import (
    DECAY_CAP,
    MIN_TOOL_SAMPLES,
    Counter,
    OutcomeLedger,
    apply_tuning,
    generation_stamp,
    record_outcome,
)
from iron_jarvis.envelope.profile import (
    CURRENT_PROBE_GENERATION,
    CapabilityProfile,
    trusted_profile,
)
from iron_jarvis.envelope.store import save_profile

PROBED_AT = "2026-08-23T00:00:00+00:00"


def _probed(**kw) -> CapabilityProfile:
    base = dict(
        model_id="qwen3:30b",
        provider="ollama",
        source="probed",
        probed_at=PROBED_AT,
        tool_protocols={"native": 0.97, "strict_json": 0.95},
        json_adherence=0.96,
        probe_generation=CURRENT_PROBE_GENERATION,
    )
    base.update(kw)
    return CapabilityProfile(**base)


def _ledger(profile: CapabilityProfile | None = None, **tools) -> OutcomeLedger:
    profile = profile if profile is not None else _probed()
    ledger = OutcomeLedger(
        model_id=profile.model_id,
        provider=profile.provider,
        profile_stamp=generation_stamp(profile),
    )
    for rung, (attempts, failures) in tools.items():
        ledger.tool_protocols[rung] = Counter(attempts=attempts, failures=failures)
    return ledger


# --------------------------------------------------------------------------- #
# Counter: rates + the halving decay bound
# --------------------------------------------------------------------------- #


def test_counter_records_and_rates():
    c = Counter()
    assert c.success_rate() == 1.0  # no evidence must never look like failure
    for ok in (True, True, False, True):
        c.record(ok)
    assert (c.attempts, c.failures) == (4, 1)
    assert c.success_rate() == 0.75


def test_counter_decays_by_halving_past_the_cap():
    c = Counter(attempts=DECAY_CAP, failures=60)
    c.record(False)  # crosses the cap -> both halve, ratio roughly preserved
    assert c.attempts == (DECAY_CAP + 1) // 2
    assert c.failures == 61 // 2
    assert 0.0 < c.success_rate() < 1.0


def test_the_ledger_is_bounded_however_many_outcomes_land():
    ledger = OutcomeLedger(model_id="m", provider="ollama")
    for i in range(5 * DECAY_CAP):
        ledger.record_tool_attempt("native", i % 3 != 0)
    assert ledger.tool_protocols["native"].attempts <= DECAY_CAP
    # ...and rungs outside the ladder are refused, so the key set is pinned
    # (half of the sidecar's size bound).
    ledger.record_tool_attempt("made-up-rung", True)
    assert set(ledger.tool_protocols) == {"native"}


# --------------------------------------------------------------------------- #
# Generation stamps: reset on change, invariant under tuning
# --------------------------------------------------------------------------- #


def test_ensure_stamp_keeps_counters_on_match_and_resets_on_new_probe():
    ledger = _ledger(native=(20, 4))
    assert ledger.ensure_stamp(generation_stamp(_probed())) is False  # match
    assert ledger.tool_protocols["native"].attempts == 20
    fresh = _probed(probed_at="2026-08-24T00:00:00+00:00")  # a NEW probe landed
    assert ledger.ensure_stamp(generation_stamp(fresh)) is True
    assert ledger.tool_protocols == {}
    assert ledger.profile_stamp == generation_stamp(fresh)


def test_a_probe_generation_bump_alone_resets_the_evidence():
    # THE Wave-C rule: same probed_at, same source — the rung semantics
    # changed (gen 1 -> gen 2), so evidence collected under the old semantics
    # is void and the ledger starts over.
    old = _probed(probe_generation=1)
    ledger = _ledger(old, native=(50, 2), strict_json=(30, 1))
    bumped = _probed(probe_generation=CURRENT_PROBE_GENERATION)
    assert generation_stamp(old) != generation_stamp(bumped)
    assert ledger.ensure_stamp(generation_stamp(bumped)) is True
    assert ledger.tool_protocols == {}


def test_generation_stamp_is_invariant_under_tuning():
    profile = _probed()
    ledger = _ledger(native=(20, 4))
    tuned = apply_tuning(profile, ledger).profile
    assert tuned.source == "tuned"
    assert tuned.probed_at == PROBED_AT  # the base measurement stands
    assert tuned.probe_generation == profile.probe_generation
    assert generation_stamp(tuned) == generation_stamp(profile)  # no self-reset


def test_generation_stamp_distinguishes_default_seeded_and_probes():
    default = CapabilityProfile(model_id="m")
    seeded = CapabilityProfile(model_id="m", source="seeded")
    assert generation_stamp(default) != generation_stamp(seeded)
    assert generation_stamp(_probed()) != generation_stamp(seeded)
    reprobed = _probed(probed_at="2026-08-25T00:00:00+00:00")
    assert generation_stamp(_probed()) != generation_stamp(reprobed)


# --------------------------------------------------------------------------- #
# apply_tuning: the downgrade-only rules (IronCore's exact hysteresis)
# --------------------------------------------------------------------------- #


def test_below_min_samples_changes_nothing():
    profile = _probed()
    result = apply_tuning(profile, _ledger(native=(MIN_TOOL_SAMPLES - 1, MIN_TOOL_SAMPLES - 1)))
    assert result.profile == profile
    assert result.profile.source == "probed"
    assert result.adjustments == []


def test_failing_live_rate_lowers_the_score_and_flips_the_ladder():
    profile = _probed()
    assert profile.select_tool_protocol() == "native"
    result = apply_tuning(profile, _ledger(native=(20, 4)))  # live rate 0.80
    tuned = result.profile
    assert tuned.tool_protocols["native"] == 0.80  # min(stored 0.97, live 0.80)
    assert tuned.select_tool_protocol() == "strict_json"  # the LADDER decided
    assert tuned.source == "tuned"
    assert tuned.probed_at == PROBED_AT  # preserved: measured, then lowered
    assert result.adjustments and "native" in result.adjustments[0]


def test_a_good_ledger_never_raises_a_score():
    # DOWNGRADE-ONLY, the headline: stored native 0.5 (below its bar), live
    # evidence spotless over 50 attempts — the score must NOT move.
    profile = _probed(tool_protocols={"native": 0.5, "strict_json": 0.95})
    result = apply_tuning(profile, _ledger(profile, native=(50, 0)))
    assert result.profile.tool_protocols["native"] == 0.5
    assert result.adjustments == []
    assert result.profile.source == "probed"  # nothing adjusted, no relabel


def test_rung_already_below_threshold_is_not_double_downgraded():
    profile = _probed(tool_protocols={"native": 0.5, "strict_json": 0.95})
    result = apply_tuning(profile, _ledger(profile, native=(20, 10)))
    assert result.profile.tool_protocols["native"] == 0.5  # untouched
    assert result.adjustments == []


def test_both_ij_rungs_tune_the_floor_is_below_the_ladder():
    # IronCore skipped its LAST rung (the unconditional text floor); Iron
    # Jarvis's floor is "none" below the ladder, so strict_json tunes too.
    profile = _probed(tool_protocols={"native": 0.5, "strict_json": 0.95})
    assert profile.select_tool_protocol() == "strict_json"
    result = apply_tuning(profile, _ledger(profile, strict_json=(20, 5)))  # 0.75
    assert result.profile.tool_protocols["strict_json"] == 0.75
    assert result.profile.select_tool_protocol() == "none"
    assert result.profile.source == "tuned"


def test_perfect_live_rate_emits_a_reprobe_hint_and_edits_nothing():
    profile = _probed(tool_protocols={"native": 0.0, "strict_json": 0.95})
    assert profile.select_tool_protocol() == "strict_json"
    result = apply_tuning(profile, _ledger(profile, strict_json=(100, 0)))
    assert result.profile == profile  # byte-identical: upgrades are NEVER applied
    assert result.profile.source == "probed"
    assert result.reprobe_hints and "native" in result.reprobe_hints[0]
    assert "re-probe" in result.reprobe_hints[0]


def test_input_profile_is_never_mutated():
    profile = _probed()
    snapshot = profile.copy()
    apply_tuning(profile, _ledger(native=(20, 20), strict_json=(20, 20)))
    assert profile == snapshot


def test_stamp_mismatch_returns_the_input_unchanged():
    # Evidence against ANOTHER generation is void — including the Wave-C
    # shape: counters collected against the gen-1 profile, profile re-probed
    # under gen 2. Bad live numbers must not re-downgrade the fresh probe.
    profile = _probed()
    ledger = _ledger(native=(20, 20))
    ledger.profile_stamp = generation_stamp(_probed(probe_generation=1))
    result = apply_tuning(profile, ledger)
    assert result.profile == profile
    assert result.adjustments == [] and result.reprobe_hints == []


def test_another_models_or_providers_ledger_is_ignored():
    profile = _probed()
    other_model = _ledger(native=(20, 20))
    other_model.model_id = "someone-else"
    assert apply_tuning(profile, other_model).profile == profile
    other_provider = _ledger(native=(20, 20))
    other_provider.provider = "custom"  # same model id behind another endpoint
    assert apply_tuning(profile, other_provider).profile == profile


def test_unmeasured_and_trusted_profiles_are_never_tuned():
    floor = CapabilityProfile(model_id="m", provider="ollama")
    ledger = OutcomeLedger(
        model_id="m", provider="ollama", profile_stamp=generation_stamp(floor)
    )
    ledger.record_tool_attempt("native", False)
    assert apply_tuning(floor, ledger).profile == floor
    trusted = trusted_profile("anthropic", "claude-opus-4-8")
    tl = OutcomeLedger(
        model_id=trusted.model_id, provider="anthropic",
        profile_stamp=generation_stamp(trusted),
    )
    for _ in range(50):
        tl.record_tool_attempt("native", False)
    result = apply_tuning(trusted, tl)
    assert result.profile == trusted  # a grant by construction is not evidence
    assert result.adjustments == []


def test_tuning_a_tuned_profile_is_stable():
    # Consult N tunes the disk profile; re-running on the tuned copy (same
    # ledger) must not compound: the score is already below threshold, and
    # the tuned overlay carries its base's stamp so the gate still passes.
    profile = _probed()
    ledger = _ledger(native=(20, 4))
    first = apply_tuning(profile, ledger).profile
    second = apply_tuning(first, ledger)
    assert second.profile.tool_protocols["native"] == first.tool_protocols["native"]
    assert second.adjustments == []


# --------------------------------------------------------------------------- #
# Persistence: the sidecar next to the profile store
# --------------------------------------------------------------------------- #


def test_save_load_round_trip_and_sidecar_naming(tmp_path):
    ledger = _ledger(_probed(model_id="qwen3:30b/instruct"), native=(12, 3))
    ledger.model_id = "qwen3:30b/instruct"
    path = ledger.save(tmp_path)
    assert path.parent == tmp_path / "envelopes"  # next to the profile JSONs
    assert path.name == "ollama__qwen3_30b_instruct.outcomes.json"  # sanitized flat
    loaded = OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b/instruct")
    assert loaded == ledger
    assert loaded.tool_protocols["native"].attempts == 12


def test_load_missing_or_corrupt_is_a_fresh_ledger_never_a_raise(tmp_path):
    fresh = OutcomeLedger.load(tmp_path, "ollama", "never-seen")
    assert fresh.model_id == "never-seen" and fresh.tool_protocols == {}
    path = OutcomeLedger.path_for(tmp_path, "ollama", "m")
    path.parent.mkdir(parents=True)
    for garbage in ("{not json", '["a", "list"]', '{"model_id": 42}'):
        path.write_text(garbage, encoding="utf-8")
        loaded = OutcomeLedger.load(tmp_path, "ollama", "m")
        assert loaded.model_id == "m" and loaded.provider == "ollama"
        assert loaded.tool_protocols == {}
    # not-UTF-8 bytes too (power-loss garbage) — never a crash.
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert OutcomeLedger.load(tmp_path, "ollama", "m").tool_protocols == {}


def test_malformed_and_off_ladder_counters_are_dropped_on_load(tmp_path):
    path = OutcomeLedger.path_for(tmp_path, "ollama", "m")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "model_id": "m", "provider": "ollama", "profile_stamp": "s",
        "tool_protocols": {
            "native": {"attempts": 12, "failures": 3},
            "strict_json": {"attempts": "many", "failures": 0},  # malformed
            "text_protocol": {"attempts": 5, "failures": 0},  # not an IJ rung
            "weird": "not-a-counter",
        },
    }))
    loaded = OutcomeLedger.load(tmp_path, "ollama", "m")
    assert set(loaded.tool_protocols) == {"native"}
    assert loaded.tool_protocols["native"].attempts == 12
    # failures can never exceed attempts, whatever the file claimed.
    path.write_text(json.dumps({
        "model_id": "m", "provider": "ollama",
        "tool_protocols": {"native": {"attempts": 3, "failures": 9}},
    }))
    counter = OutcomeLedger.load(tmp_path, "ollama", "m").tool_protocols["native"]
    assert counter.failures <= counter.attempts
    assert 0.0 <= counter.success_rate() <= 1.0


def test_a_mismatched_sidecar_reads_as_fresh(tmp_path):
    ledger = _ledger(native=(9, 1))
    ledger.save(tmp_path)
    # Another (provider, model) landing on the same path can only happen via
    # hand-editing; identity mismatch -> fresh, never someone else's evidence.
    path = OutcomeLedger.path_for(tmp_path, "ollama", "qwen3:30b")
    data = json.loads(path.read_text())
    data["model_id"] = "someone-else"
    path.write_text(json.dumps(data))
    assert OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b").tool_protocols == {}


# --------------------------------------------------------------------------- #
# record_outcome: the public never-raising seam the runtime calls
# --------------------------------------------------------------------------- #


def test_record_outcome_writes_the_sidecar_and_attributes_the_active_rung(tmp_path):
    save_profile(tmp_path, _probed())  # selects native (gen-current, 0.97)
    record_outcome(tmp_path, "ollama", "qwen3:30b", True)
    record_outcome(tmp_path, "ollama", "qwen3:30b", False)
    ledger = OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b")
    assert ledger.profile_stamp == generation_stamp(_probed())
    counter = ledger.tool_protocols["native"]
    assert (counter.attempts, counter.failures) == (2, 1)
    # An explicit protocol wins over the selected rung (the retry ladder may
    # have driven strict_json on a retry).
    record_outcome(tmp_path, "ollama", "qwen3:30b", False, protocol="strict_json")
    ledger = OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b")
    assert ledger.tool_protocols["strict_json"].failures == 1


def test_record_outcome_is_silent_with_nothing_measured(tmp_path):
    # No stored profile at all; a seeded profile; a floor: no rung to
    # attribute evidence to, and the tuner could never consume it -> no file.
    record_outcome(tmp_path, "ollama", "never-probed", False)
    save_profile(tmp_path, CapabilityProfile(
        model_id="seeded-m", provider="ollama", source="seeded",
        tool_protocols={"native": 0.95},
    ))
    record_outcome(tmp_path, "ollama", "seeded-m", False)
    assert not list((tmp_path / "envelopes").glob("*.outcomes.json"))


def test_record_outcome_skips_the_none_rung(tmp_path):
    # Measured but nothing cleared a bar: select_tool_protocol() == "none" —
    # there is no rung driving the calls, so nothing is recorded.
    save_profile(tmp_path, _probed(tool_protocols={"native": 0.5, "strict_json": 0.5}))
    record_outcome(tmp_path, "ollama", "qwen3:30b", False)
    assert not list((tmp_path / "envelopes").glob("*.outcomes.json"))


def test_record_outcome_never_raises(tmp_path):
    # A home that is a FILE (every open fails), and a home that does not
    # exist: both swallowed — evidence is an optimization, not a dependency.
    bogus = tmp_path / "not-a-dir"
    bogus.write_text("x")
    record_outcome(bogus, "ollama", "m", True)
    record_outcome(tmp_path / "nowhere" / "deeper", "ollama", "m", True)


def test_record_outcome_resets_evidence_when_the_generation_bumped(tmp_path):
    # End to end: outcomes collected against a gen-1 record; the record is
    # re-probed under gen 2; the NEXT outcome resets the ledger first — old
    # bare-prompt evidence must never re-downgrade the fresh measurement.
    gen1 = _probed(probe_generation=1)
    save_profile(tmp_path, gen1)
    for _ in range(30):
        record_outcome(tmp_path, "ollama", "qwen3:30b", False)
    assert (
        OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b")
        .tool_protocols["native"].failures == 30
    )
    save_profile(tmp_path, _probed())  # the gen-2 re-probe landed
    record_outcome(tmp_path, "ollama", "qwen3:30b", True)
    ledger = OutcomeLedger.load(tmp_path, "ollama", "qwen3:30b")
    assert ledger.profile_stamp == generation_stamp(_probed())
    counter = ledger.tool_protocols["native"]
    assert (counter.attempts, counter.failures) == (1, 0)  # history voided
    # ...and tuning off that fresh single sample does nothing (hysteresis).
    result = apply_tuning(_probed(), ledger)
    assert result.adjustments == []
