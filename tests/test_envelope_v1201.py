"""Capability Envelope foundation (v1.201.0, Wave A1+A5-backend).

Offline, fake-transport tests for the ported IronCore envelope: provenance
pins (probe_failed never stamps probed_at; partial vs probed; a failed
re-probe keeps the last good measurement), mechanical ladder selection,
loop-bending bands incl. the Iron-Jarvis-only "trusted" source, store
atomicity + never-raising loads + filename sanitization, seeding from a
faked /api/show, and mechanical probe scoring on canned outputs.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from iron_jarvis.envelope import store
from iron_jarvis.envelope.probes import (
    JsonStrictProbe,
    ProbeReply,
    TokenRatioProbe,
    ToolFormProbe,
    _conforms,
    quick_battery,
)
from iron_jarvis.envelope.profile import (
    SOURCES,
    TOOL_PROTOCOL_THRESHOLDS,
    CapabilityProfile,
    trusted_profile,
)
from iron_jarvis.envelope.runner import run_quick_battery
from iron_jarvis.envelope.seed import seed_profile

GOOD_CALL = ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "Paris", "units": "celsius"}}])
BAD_CALL = ProbeReply(tool_calls=[{"name": "get_weather", "arguments": {"city": "London", "units": "celsius"}}])
GOOD_JSON = ProbeReply(text='{"tool": "get_weather", "args": {"city": "Paris", "units": "celsius"}}')
PROSE = ProbeReply(text="Sure! I'd be happy to call that tool for you.")
GOOD_SCHEMA = ProbeReply(text='{"title": "Ship the release", "priority": 2, "done": false, "tags": ["release"]}')


def scripted(replies):
    """A transport that returns canned replies in FIFO order, whatever the
    prompt says (the IronCore MockProvider pattern)."""
    queue = list(replies)

    async def complete(messages, **kw):
        return queue.pop(0)

    return complete


def failing_transport(exc=None):
    async def complete(messages, **kw):
        raise exc or ConnectionError("endpoint down")

    return complete


def measured_profile(**overrides) -> CapabilityProfile:
    """A cached record that looks like a real earlier measurement."""
    base = dict(
        model_id="qwen3:30b/instruct",
        provider="ollama",
        source="probed",
        probed_at="2026-08-01T00:00:00+00:00",
        context_window=32768,
        honest_context=12400,
        chars_per_token=3.6,
        tool_protocols={"native": 0.98, "strict_json": 0.95},
        json_adherence=0.97,
        coherence_horizon=9,
        measured_fields=[
            "chars_per_token",
            "coherence_horizon",
            "honest_context",
            "json_adherence",
            "tool_protocols.native",
            "tool_protocols.strict_json",
        ],
    )
    base.update(overrides)
    return CapabilityProfile(**base)


# --------------------------------------------------------------------------- #
# profile: ladder selection + provenance vocabulary
# --------------------------------------------------------------------------- #


def test_source_vocabulary_has_all_seven_values():
    assert set(SOURCES) == {
        "default", "seeded", "probed", "partial", "probe_failed", "tuned", "trusted",
    }


def test_ladder_picks_native_at_its_bar():
    p = CapabilityProfile(model_id="m", tool_protocols={"native": 0.95, "strict_json": 0.99})
    assert p.select_tool_protocol() == "native"


def test_ladder_falls_to_strict_json_when_native_misses_its_bar():
    p = CapabilityProfile(model_id="m", tool_protocols={"native": 0.9499, "strict_json": 0.90})
    assert p.select_tool_protocol() == "strict_json"


def test_ladder_floor_is_none_when_nothing_clears():
    p = CapabilityProfile(model_id="m", tool_protocols={"native": 0.5, "strict_json": 0.85})
    assert p.select_tool_protocol() == "none"
    assert CapabilityProfile(model_id="m").select_tool_protocol() == "none"


def test_thresholds_are_ironcores_proven_bars():
    assert TOOL_PROTOCOL_THRESHOLDS == {"native": 0.95, "strict_json": 0.90}


# --------------------------------------------------------------------------- #
# profile: loop-bending bands
# --------------------------------------------------------------------------- #


def test_max_tools_is_uncapped_for_trusted_and_unmeasured():
    assert trusted_profile("anthropic", "claude-opus-4-8").max_tools() is None
    # default and seeded profiles are unmeasured — the envelope only narrows
    # on evidence, so today's behavior stays byte-identical.
    assert CapabilityProfile(model_id="m").max_tools() is None
    seeded = CapabilityProfile(model_id="m", source="seeded", tool_protocols={"native": 0.95})
    assert seeded.max_tools() is None


def test_max_tools_bands_scale_from_the_measured_native_score():
    def probed(native: float) -> CapabilityProfile:
        return measured_profile(tool_protocols={"native": native})

    assert probed(0.96).max_tools() is None
    assert probed(0.95).max_tools() is None
    assert probed(0.92).max_tools() == 6
    assert probed(0.80).max_tools() == 4
    assert probed(0.50).max_tools() == 3
    assert probed(0.0).max_tools() == 3


def test_needs_decomposition_bands():
    assert trusted_profile("openai", "gpt-6").needs_decomposition() is False
    strong = measured_profile(tool_protocols={"native": 0.98}, coherence_horizon=8)
    assert strong.needs_decomposition() is False
    drifty = measured_profile(tool_protocols={"native": 0.98}, coherence_horizon=5)
    assert drifty.needs_decomposition() is True
    strict_rung = measured_profile(tool_protocols={"native": 0.5, "strict_json": 0.95})
    assert strict_rung.needs_decomposition() is True
    # conservative floor: an unmeasured, untrusted profile has no rung
    assert CapabilityProfile(model_id="m").needs_decomposition() is True


def test_verify_every_step_bands():
    assert trusted_profile("anthropic", "claude-opus-4-8").verify_every_step() is False
    weak_json = measured_profile(json_adherence=0.85)
    assert weak_json.verify_every_step() is True
    strong = measured_profile()
    assert strong.verify_every_step() is False
    # a seed's unmeasured json_adherence of 0.0 is absence of evidence, not
    # evidence of weakness: a native-seeded profile is not forced to verify.
    seeded = CapabilityProfile(
        model_id="m", source="seeded", tool_protocols={"native": 0.95}
    )
    assert seeded.verify_every_step() is False


def test_from_dict_tolerates_unknown_and_missing_fields():
    p = CapabilityProfile.from_dict(
        {"model_id": "m", "provider": "ollama", "brand_new_field": 42,
         "tool_protocols": {"native": 0.9, "junk": "not-a-score"}}
    )
    assert p.model_id == "m"
    assert p.tool_protocols == {"native": 0.9}  # non-numeric score dropped
    assert p.chars_per_token == 4.0  # missing fields load as today's defaults
    # round trip: to_dict -> from_dict is lossless for known fields
    assert CapabilityProfile.from_dict(measured_profile().to_dict()) == measured_profile()


# --------------------------------------------------------------------------- #
# store: paths, atomicity, never-raising loads
# --------------------------------------------------------------------------- #


def test_filename_sanitizes_slashes_and_colons(tmp_path):
    profile = measured_profile()  # model "qwen3:30b/instruct"
    path = store.save_profile(tmp_path, profile)
    assert path.parent == tmp_path / "envelopes"  # no subdirectory escaped from the "/"
    assert path.name == "ollama__qwen3_30b_instruct.json"
    assert store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct") == profile


def test_load_missing_is_none_not_a_raise(tmp_path):
    assert store.load_profile(tmp_path, "ollama", "nope") is None


def test_corrupt_cache_loads_none_and_is_quarantined(tmp_path):
    profile = measured_profile()
    path = store.save_profile(tmp_path, profile)
    path.write_bytes(b'{"model_id": "qwen3:30b/instr')  # truncated mid-write
    assert store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct") is None
    assert not path.exists()  # live path freed for the next probe
    assert path.with_name(path.name + ".corrupt").exists()  # evidence kept


def test_not_utf8_and_wrong_shape_both_load_none(tmp_path):
    path = store.profile_path(tmp_path, "custom", "m")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff\xfe\x00garbage")
    assert store.load_profile(tmp_path, "custom", "m") is None
    path2 = store.profile_path(tmp_path, "custom", "m2")
    path2.write_text('["a", "list", "not", "an", "object"]')
    assert store.load_profile(tmp_path, "custom", "m2") is None


def test_unknown_fields_on_disk_are_tolerated(tmp_path):
    path = store.profile_path(tmp_path, "ollama", "m")
    path.parent.mkdir(parents=True)
    payload = measured_profile(model_id="m").to_dict()
    payload["from_a_newer_version"] = {"nested": True}
    path.write_text(json.dumps(payload))
    loaded = store.load_profile(tmp_path, "ollama", "m")
    assert loaded is not None and loaded.tool_protocols["native"] == 0.98


def test_atomic_write_leaves_no_staging_droppings(tmp_path):
    store.save_profile(tmp_path, measured_profile())
    leftovers = list((tmp_path / "envelopes").glob("*.tmp")) + list(
        (tmp_path / "envelopes").glob(".*.tmp")
    )
    assert leftovers == []


# --------------------------------------------------------------------------- #
# store: keep-last-good
# --------------------------------------------------------------------------- #


def test_probe_failed_over_a_measured_record_preserves_the_scores(tmp_path):
    record = measured_profile()
    store.save_profile(tmp_path, record)
    failed = CapabilityProfile(
        model_id=record.model_id, provider=record.provider, source="probe_failed"
    )
    durable = store.save_measurement(tmp_path, failed)
    # the record's measurements survive WHOLESALE...
    assert durable.tool_protocols == {"native": 0.98, "strict_json": 0.95}
    assert durable.honest_context == 12400
    assert durable.chars_per_token == 3.6
    # ...and the failure is annotated: probe_failed, NO stamp.
    assert durable.source == "probe_failed"
    assert durable.probed_at is None
    on_disk = store.load_profile(tmp_path, record.provider, record.model_id)
    assert on_disk == durable


def test_first_ever_failure_writes_the_floor_not_the_seeds_introspection(tmp_path):
    # No record cached. The session base was a seed (window 32768) that the
    # battery failed to refine — the seed's introspection must NOT be
    # persisted through the failure.
    failed = CapabilityProfile(
        model_id="m", provider="ollama", source="probe_failed",
        context_window=32768, honest_context=32768, vision=True,
    )
    durable = store.save_measurement(tmp_path, failed)
    assert durable.context_window == 8192  # the floor, not the seed's claim
    assert durable.honest_context == 4096
    assert durable.vision is False
    assert durable.source == "probe_failed" and durable.probed_at is None


def test_partial_restores_unverified_scalars_but_never_reliabilities(tmp_path):
    record = measured_profile()
    store.save_profile(tmp_path, record)
    # a partial re-probe: JSON-STRICT died (floored 0.0), TOKEN-RATIO owed
    # chars_per_token and did not deliver it.
    partial = measured_profile(
        source="partial",
        probed_at="2026-08-22T00:00:00+00:00",
        chars_per_token=4.0,
        json_adherence=0.0,
        tool_protocols={"native": 0.96, "strict_json": 0.92},
        measured_fields=["tool_protocols.native", "tool_protocols.strict_json"],
    )
    durable = store.save_measurement(
        tmp_path, partial, unverified={"json_adherence", "chars_per_token"}
    )
    assert durable.chars_per_token == 3.6  # non-reliability: restored from the record
    assert durable.json_adherence == 0.0  # reliability: stays floored
    assert durable.tool_protocols["native"] == 0.96  # this run's own evidence stands
    assert durable.source == "partial" and durable.probed_at is not None
    # per-field provenance: the restored ratio keeps the RECORD's evidence
    # claim; the floored reliability carries none.
    assert durable.field_measured("chars_per_token") is True
    assert durable.field_measured("json_adherence") is False


def test_a_seeded_record_is_never_restored_from(tmp_path):
    seeded = measured_profile(source="seeded", probed_at=None)
    store.save_profile(tmp_path, seeded)
    partial = measured_profile(
        source="partial", chars_per_token=4.0, tool_protocols={"native": 0.96}
    )
    durable = store.save_measurement(tmp_path, partial, unverified={"chars_per_token"})
    assert durable.chars_per_token == 4.0  # the seed's 3.6 did not launder in


def test_consecutive_failures_chain_without_losing_the_measurement(tmp_path):
    store.save_profile(tmp_path, measured_profile())
    failed = CapabilityProfile(
        model_id="qwen3:30b/instruct", provider="ollama", source="probe_failed"
    )
    store.save_measurement(tmp_path, failed)
    durable = store.save_measurement(tmp_path, failed)  # second blip reads the first's record
    assert durable.tool_protocols["native"] == 0.98
    assert durable.honest_context == 12400


# --------------------------------------------------------------------------- #
# probes: mechanical scoring on canned outputs
# --------------------------------------------------------------------------- #


async def test_tool_form_scores_each_rung_as_a_fraction():
    # 3 native trials (2 correct), then 3 strict_json trials (1 correct)
    transport = scripted([GOOD_CALL, BAD_CALL, GOOD_CALL, GOOD_JSON, PROSE, PROSE])
    result = await ToolFormProbe(trials=3).run(transport)
    assert result.ok is True
    assert result.scores["tool_protocols.native"] == pytest.approx(2 / 3)
    assert result.scores["tool_protocols.strict_json"] == pytest.approx(1 / 3)


async def test_tool_form_rejects_wrong_args_and_extra_calls():
    two_calls = ProbeReply(
        tool_calls=[GOOD_CALL.tool_calls[0], GOOD_CALL.tool_calls[0]]
    )
    transport = scripted([BAD_CALL, two_calls, ProbeReply(), PROSE, GOOD_JSON, GOOD_JSON])
    result = await ToolFormProbe(trials=3).run(transport)
    assert result.scores["tool_protocols.native"] == 0.0
    assert result.scores["tool_protocols.strict_json"] == pytest.approx(2 / 3)


async def test_tool_form_transport_failure_is_ok_false_not_a_crash():
    result = await ToolFormProbe(trials=3).run(failing_transport())
    assert result.ok is False and result.scores == {}
    assert "TOOL-FORM" in result.notes


def test_json_strict_conformance_is_typed_not_just_parsed():
    assert _conforms(GOOD_SCHEMA.text) is True
    assert _conforms("not json") is False
    assert _conforms('["a", "list"]') is False
    # bool is an int subclass in Python — a schema checker that misses this
    # passes "priority": true, and "done": 1 the other way around.
    assert _conforms('{"title": "t", "priority": true, "done": false, "tags": []}') is False
    assert _conforms('{"title": "t", "priority": 2, "done": 1, "tags": []}') is False
    assert _conforms('{"title": "t", "priority": 2, "done": false}') is False  # missing key


async def test_json_strict_scores_the_conforming_fraction():
    transport = scripted([GOOD_SCHEMA, PROSE, GOOD_SCHEMA])
    result = await JsonStrictProbe(trials=3).run(transport)
    assert result.ok is True
    assert result.scores == {"json_adherence": pytest.approx(2 / 3)}


async def test_token_ratio_measures_from_server_usage_and_clamps():
    def with_usage(tokens):
        return ProbeReply(text="OK", usage={"prompt_tokens": tokens})

    probe = TokenRatioProbe(sizes=(64, 128))
    result = await probe.run(scripted([with_usage(120), with_usage(230)]))
    assert result.ok is True
    ratio = result.scores["chars_per_token"]
    assert 1.0 <= ratio <= 8.0
    # nonsense usage clamps rather than storing garbage budget math
    absurd = await TokenRatioProbe(sizes=(64,)).run(scripted([with_usage(1)]))
    assert absurd.scores["chars_per_token"] == 8.0


async def test_token_ratio_without_usage_reports_unmeasured_honestly():
    # ok=True with EMPTY scores + an explicit note — never a fabricated ratio,
    # and never ok=False (that would floor reliabilities that were never its).
    result = await TokenRatioProbe(sizes=(64, 128)).run(
        scripted([ProbeReply(text="OK"), ProbeReply(text="OK", usage={"input_tokens": 0})])
    )
    assert result.ok is True
    assert result.scores == {}
    assert "no usage" in result.notes


# --------------------------------------------------------------------------- #
# runner: provenance stamping + keep-last-good end to end
# --------------------------------------------------------------------------- #


def full_battery_replies():
    """One scripted reply per transport call for a clean quick battery run:
    TOOL-FORM (3 native + 3 strict_json), JSON-STRICT (3), TOKEN-RATIO (3)."""
    usage = ProbeReply(text="OK", usage={"prompt_tokens": 400})
    return [GOOD_CALL] * 3 + [GOOD_JSON] * 3 + [GOOD_SCHEMA] * 3 + [usage] * 3


def base_profile() -> CapabilityProfile:
    return CapabilityProfile(model_id="qwen3:30b/instruct", provider="ollama")


async def test_clean_battery_stamps_probed_with_probed_at(tmp_path):
    session = await run_quick_battery(
        base_profile(), scripted(full_battery_replies()),
        home=tmp_path, probed_at="2026-08-22T12:00:00+00:00",
    )
    assert session.source == "probed"
    assert session.probed_at == "2026-08-22T12:00:00+00:00"
    assert session.tool_protocols == {"native": 1.0, "strict_json": 1.0}
    assert session.json_adherence == 1.0
    assert session.chars_per_token != 4.0  # measured, not the default
    saved = store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct")
    assert saved == session


async def test_probed_stamp_never_launders_the_unmeasured_window(tmp_path):
    # THE ship-blocker pin: the quick battery never measures honest_context or
    # context_window, so a "probed" profile must answer field_measured False
    # for them — a consumer that trusted the stamp shrank a 128k model's
    # window to the base's 4096 floor with one Measure click.
    session = await run_quick_battery(
        base_profile(), scripted(full_battery_replies()),
        home=tmp_path, probed_at="2026-08-22T12:00:00+00:00",
    )
    assert session.source == "probed"  # the battery ran...
    assert session.measured_fields == [  # ...and delivered exactly its four targets
        "chars_per_token",
        "json_adherence",
        "tool_protocols.native",
        "tool_protocols.strict_json",
    ]
    assert "honest_context" not in session.measured_fields
    assert session.field_measured("honest_context") is False
    assert session.field_measured("context_window") is False
    assert session.field_measured("chars_per_token") is True
    assert session.field_measured("tool_protocols") is True  # root query
    assert session.field_measured("tool_protocols.native") is True
    saved = store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct")
    assert saved is not None and saved.field_measured("honest_context") is False


async def test_partial_delivery_marks_only_the_delivered_fields():
    class Dying:
        id = "JSON-STRICT"
        targets = ("json_adherence",)

        async def run(self, transport):
            raise RuntimeError("boom")

    battery = [ToolFormProbe(trials=3), Dying(), TokenRatioProbe(sizes=(64,))]
    replies = [GOOD_CALL] * 3 + [GOOD_JSON] * 3 + [ProbeReply(text="OK")]  # no usage
    session = await run_quick_battery(base_profile(), scripted(replies), probes=battery)
    assert session.source == "partial"
    # only TOOL-FORM delivered: the dead probe's target is floored (not
    # evidence) and the no-usage TOKEN-RATIO delivered nothing despite ok=True.
    assert session.measured_fields == ["tool_protocols.native", "tool_protocols.strict_json"]
    assert session.field_measured("json_adherence") is False
    assert session.field_measured("chars_per_token") is False


def test_wholesale_carry_preserves_the_records_measured_fields(tmp_path):
    store.save_profile(tmp_path, measured_profile())
    failed = CapabilityProfile(
        model_id="qwen3:30b/instruct", provider="ollama", source="probe_failed"
    )
    durable = store.save_measurement(tmp_path, failed)
    # the values survived wholesale, so their provenance does too
    assert durable.field_measured("honest_context") is True
    assert durable.field_measured("tool_protocols.native") is True
    assert durable.measured_fields == measured_profile().measured_fields


def test_floor_anchor_carries_no_measured_fields(tmp_path):
    failed = CapabilityProfile(
        model_id="m", provider="ollama", source="probe_failed",
        measured_fields=["honest_context"],  # a hand-built lie the floor must not keep
    )
    durable = store.save_measurement(tmp_path, failed)
    assert durable.measured_fields == []
    assert durable.field_measured("honest_context") is False


def test_pre_measured_fields_json_loads_as_all_unmeasured(tmp_path):
    path = store.profile_path(tmp_path, "ollama", "legacy")
    path.parent.mkdir(parents=True)
    payload = measured_profile(model_id="legacy").to_dict()
    del payload["measured_fields"]  # written before per-field provenance existed
    path.write_text(json.dumps(payload))
    loaded = store.load_profile(tmp_path, "ollama", "legacy")
    assert loaded is not None
    assert loaded.measured_fields == []
    for name in ("honest_context", "chars_per_token", "json_adherence",
                 "tool_protocols", "context_window", "coherence_horizon"):
        assert loaded.field_measured(name) is False


def test_trusted_profile_claims_capability_but_no_evidence():
    trusted = trusted_profile("anthropic", "claude-opus-4-8")
    assert trusted.measured_fields == []
    assert trusted.field_measured("tool_protocols") is False
    assert trusted.max_tools() is None  # capability by construction, unchanged


async def test_one_dead_probe_stamps_partial_with_probed_at():
    class Dying:
        id = "JSON-STRICT"
        targets = ("json_adherence",)

        async def run(self, transport):
            raise RuntimeError("boom")

    battery = [ToolFormProbe(trials=3), Dying(), TokenRatioProbe(sizes=(64,))]
    replies = [GOOD_CALL] * 3 + [GOOD_JSON] * 3 + [ProbeReply(text="OK", usage={"prompt_tokens": 90})]
    session = await run_quick_battery(
        base_profile(), scripted(replies), probes=battery, probed_at="2026-08-22T12:00:00+00:00"
    )
    assert session.source == "partial"
    assert session.probed_at is not None  # something WAS measured
    assert session.json_adherence == 0.0  # the dead probe's reliability floored


async def test_total_failure_stamps_probe_failed_and_never_probed_at():
    session = await run_quick_battery(
        base_profile(), failing_transport(), probed_at="2026-08-22T12:00:00+00:00"
    )
    assert session.source == "probe_failed"
    assert session.probed_at is None  # the provenance pin: failed is not measured
    # session reliabilities are floored — the live loop must drive the floor
    assert session.tool_protocols == {"native": 0.0, "strict_json": 0.0}
    assert session.json_adherence == 0.0
    # non-reliabilities keep the base: a failed measurement invents nothing
    assert session.chars_per_token == 4.0


async def test_empty_battery_is_probe_failed_not_a_vacuous_probed(tmp_path):
    store.save_profile(tmp_path, measured_profile())
    session = await run_quick_battery(
        base_profile(), scripted([]), probes=[], home=tmp_path
    )
    assert session.source == "probe_failed" and session.probed_at is None
    # ...and the record survived the vacuous run
    saved = store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct")
    assert saved is not None and saved.tool_protocols["native"] == 0.98


async def test_failed_reprobe_keeps_the_last_good_record_on_disk(tmp_path):
    store.save_profile(tmp_path, measured_profile())
    session = await run_quick_battery(
        base_profile(), failing_transport(), home=tmp_path
    )
    # the SESSION drives the floor...
    assert session.tool_protocols == {"native": 0.0, "strict_json": 0.0}
    # ...while the RECORD keeps the measurement, annotated with the failure.
    saved = store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct")
    assert saved is not None
    assert saved.tool_protocols == {"native": 0.98, "strict_json": 0.95}
    assert saved.honest_context == 12400
    assert saved.source == "probe_failed" and saved.probed_at is None


async def test_no_usage_reprobe_keeps_a_measured_token_ratio(tmp_path):
    # The IC-1214 headline: a CLEAN battery (every probe ok=True) whose
    # server omits usage must not overwrite a measured chars_per_token 3.6
    # with the 4.0 default — coverage, not probe survival, is what counts.
    store.save_profile(tmp_path, measured_profile())
    replies = [GOOD_CALL] * 3 + [GOOD_JSON] * 3 + [GOOD_SCHEMA] * 3 + [ProbeReply(text="OK")] * 3
    session = await run_quick_battery(
        base_profile(), scripted(replies), home=tmp_path,
        probed_at="2026-08-22T12:00:00+00:00",
    )
    assert session.source == "probed"  # every probe answered
    assert session.chars_per_token == 4.0  # the session honestly holds the default
    assert session.field_measured("chars_per_token") is False  # ok=True is not coverage
    saved = store.load_profile(tmp_path, "ollama", "qwen3:30b/instruct")
    assert saved is not None
    assert saved.chars_per_token == 3.6  # the record kept the measurement
    assert saved.field_measured("chars_per_token") is True  # with its provenance


async def test_battery_deadline_degrades_instead_of_hanging():
    class Sleeper:
        id = "SLEEPER"
        targets = ("tool_protocols.native",)

        async def run(self, transport):
            await asyncio.sleep(30)

    class Never:
        id = "NEVER-ANSWERS"
        targets = ("json_adherence",)

        async def run(self, transport):
            await asyncio.sleep(30)

    session = await run_quick_battery(
        base_profile(), scripted([]), probes=[Sleeper(), Never()], total_timeout=0.05
    )
    # both degraded, exactly one result each -> a total failure, honestly stamped
    assert session.source == "probe_failed" and session.probed_at is None
    assert session.tool_protocols == {"native": 0.0}
    assert session.json_adherence == 0.0


async def test_quick_battery_declares_every_loop_bending_target():
    declared = set()
    for probe in quick_battery():
        declared.update(probe.targets)
    assert declared == {
        "tool_protocols.native",
        "tool_protocols.strict_json",
        "json_adherence",
        "chars_per_token",
    }


# --------------------------------------------------------------------------- #
# seed: instant-on introspection against faked endpoints
# --------------------------------------------------------------------------- #


def ollama_mock(model_info=None, capabilities=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            body = {}
            if model_info is not None:
                body["model_info"] = model_info
            if capabilities is not None:
                body["capabilities"] = capabilities
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_seed_from_a_faked_api_show():
    client = ollama_mock(
        model_info={"qwen3.context_length": 40960, "general.architecture": "qwen3"},
        capabilities=["completion", "tools", "vision"],
    )
    profile = await seed_profile(
        "ollama", "qwen3:30b", "http://127.0.0.1:11434/v1", client=client
    )
    assert profile is not None
    assert profile.source == "seeded"
    assert profile.probed_at is None  # a seed is never a measurement
    assert profile.context_window == 40960
    assert profile.honest_context == 32768  # capped: unmeasured depth is a claim
    assert profile.vision is True
    assert profile.tool_protocols == {"native": 0.95}  # clears the bar, provisionally
    assert profile.select_tool_protocol() == "native"


async def test_seed_without_tools_or_vision_keeps_the_floor():
    client = ollama_mock(model_info={"llama.context_length": 8192}, capabilities=["completion"])
    profile = await seed_profile("ollama", "llama3.1", "http://x:11434", client=client)
    assert profile is not None
    assert profile.vision is False
    assert profile.tool_protocols == {}
    assert profile.select_tool_protocol() == "none"


async def test_seed_falls_back_to_openai_compat_models_listing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": "my-model", "max_model_len": 16384}]}
            )
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    profile = await seed_profile("custom", "my-model", "http://lan-box:8000/v1", client=client)
    assert profile is not None
    assert profile.source == "seeded"
    assert profile.context_window == 16384
    assert profile.tool_protocols == {}  # presence only — no capability claims


async def test_seed_never_raises_and_answers_none_when_nothing_answers():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await seed_profile("ollama", "m", "http://127.0.0.1:1", client=client) is None
    assert await seed_profile("ollama", "m", "", client=client) is None
