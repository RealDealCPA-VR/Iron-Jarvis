"""ProviderManager consults the capability envelope (v1.201.0, Wave A3).

The ONE behavior change this wave: ``daemon/chat_turn._context_window`` gains
the MEASURED envelope between the config pin and the fleet probe. Everything
else here pins the new accessors (``capability_profile`` /
``measured_context_window``) so later waves can consult them:

* pin-beats-envelope; measured-envelope-beats-probe/default;
* seeded / trusted / default profiles DO NOT alter the window (separate pins
  each — seeded honest_context is a capped guess, trusted_profile is
  documented as not a window authority, default is the floor);
* THE WAVE-A SHIP-BLOCKER'S PIN: a quick-battery profile (source="probed" +
  probed_at, but honest_context NOT in measured_fields — the quick battery
  never measures it) must NOT speak a window; without the per-field gate one
  Measure click shrank a 128k model's _context_window to the 4096 floor;
* no-profile behavior is byte-identical to the pre-envelope ladder
  (pin -> fleet probe -> None), asserted against a providers-less platform;
* every API/cli provider the manager itself knows (plus mock) is trusted,
  and ``is_trusted_provider`` is the PUBLIC single oracle for that set;
* the per-(provider, model) cache invalidates on a store-file change
  (mtime/size signature — the design documented in ``capability_profile``),
  and does NOT re-parse when the file is unchanged.
"""

from __future__ import annotations

from pathlib import Path

import iron_jarvis.providers.manager as manager_mod
from iron_jarvis.daemon.chat_turn import _context_window
from iron_jarvis.envelope import store
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.providers.manager import API_PROVIDERS, ProviderManager

#: A profile whose window IS a measurement: the deep-CTX-battery shape
#: (honest_context named in measured_fields). The quick battery never
#: delivers this — see QUICK_BATTERY below.
MEASURED = dict(
    source="probed",
    probed_at="2026-08-22T10:00:00Z",
    honest_context=20000,
    measured_fields=["honest_context"],
)

#: Exactly what a clean QUICK battery delivers today (runner.py): the two
#: tool rungs, json_adherence, chars_per_token — and NEVER honest_context.
QUICK_BATTERY = dict(
    source="probed",
    probed_at="2026-08-22T10:00:00Z",
    honest_context=4096,  # still the floor: nothing measured it
    measured_fields=[
        "chars_per_token",
        "json_adherence",
        "tool_protocols.native",
        "tool_protocols.strict_json",
    ],
)


def _save(home, provider, model, **kw) -> CapabilityProfile:
    profile = CapabilityProfile(model_id=model, provider=provider, **kw)
    store.save_profile(Path(home), profile)
    return profile


# --------------------------------------------------------------------------- #
# Minimal fake platform for _context_window (the function reads exactly
# d.platform.config / d.platform.providers / d.platform.fleet)
# --------------------------------------------------------------------------- #


class _Cfg:
    def __init__(self, pins=None):
        self.model_context_windows = dict(pins or {})
        self.default_provider = ""
        self.default_model = ""


class _Model:
    def __init__(self, name, context_length):
        self.name = name
        self.context_length = context_length


class _Node:
    def __init__(self, models):
        self.models = models


class _Fleet:
    def __init__(self, nodes):
        self._nodes = nodes

    def nodes(self):
        return self._nodes


class _Platform:
    def __init__(self, config, providers=None, fleet=None):
        self.config = config
        self.providers = providers
        self.fleet = fleet


class _D:
    def __init__(self, platform):
        self.platform = platform


def _d(pins=None, providers=None, fleet=None):
    return _D(_Platform(_Cfg(pins), providers=providers, fleet=fleet))


def _fleet(model="qwen3:30b", context_length=32_768):
    return _Fleet([_Node([_Model(model, context_length)])])


# --------------------------------------------------------------------------- #
# capability_profile: the provider taxonomy
# --------------------------------------------------------------------------- #


def test_capability_profile_trusted_for_every_api_cli_and_mock_provider(tmp_path):
    mgr = ProviderManager(envelope_home=tmp_path)
    cli_names = sorted(n for n in mgr._factories if n.endswith("-cli"))
    # The manager's OWN registrations, not a list this test invented — if a
    # CLI provider is added it must land in the trusted set automatically.
    assert set(cli_names) >= {"grok-cli", "claude-cli", "codex-cli", "opencode-cli"}
    for name in (*API_PROVIDERS, *cli_names, "mock"):
        profile = mgr.capability_profile(name, "whatever-model")
        assert profile.source == "trusted", name
        assert profile.is_trusted(), name
        assert profile.provider == name
        # Trusted = zero loop-bending by construction.
        assert profile.max_tools() is None, name
        assert not profile.needs_decomposition(), name


def test_trusted_wins_even_over_a_stored_measured_profile(tmp_path):
    # A profile file for a cloud provider (however it got there) must not
    # demote it: trusted is BY CONSTRUCTION, the store is never consulted.
    _save(tmp_path, "anthropic", "claude-opus-4-8", **MEASURED)
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.capability_profile("anthropic", "claude-opus-4-8").source == "trusted"
    assert mgr.measured_context_window("anthropic", "claude-opus-4-8") is None


def test_local_providers_are_not_trusted(tmp_path):
    mgr = ProviderManager(envelope_home=tmp_path)
    for name in ("ollama", "custom", "fleet-workstation"):
        assert mgr.capability_profile(name, "m").source == "default", name


def test_is_trusted_provider_is_the_public_single_oracle(tmp_path):
    """Reviewer defect 4: the trusted set must have ONE oracle. This is the
    public surface the envelope routes (and every later wave) consume — a
    private copy elsewhere already drifted on mock and would drift on every
    future CLI. Pins the method's existence, name, and verdicts."""
    mgr = ProviderManager(envelope_home=tmp_path)
    for name in (*API_PROVIDERS, "grok-cli", "claude-cli", "codex-cli",
                 "opencode-cli", "some-future-cli", "mock"):
        assert mgr.is_trusted_provider(name) is True, name
    for name in ("ollama", "custom", "fleet-x", "fleet-workstation", "", "auto"):
        assert mgr.is_trusted_provider(name) is False, name


# --------------------------------------------------------------------------- #
# capability_profile: floor / store / never-raises
# --------------------------------------------------------------------------- #


def test_bare_manager_answers_the_floor_and_touches_no_disk():
    mgr = ProviderManager()  # hermetic: no envelope_home
    profile = mgr.capability_profile("ollama", "llama3.1")
    assert profile.source == "default"
    assert profile.honest_context == 4096
    assert mgr.measured_context_window("ollama", "llama3.1") is None


def test_missing_profile_answers_the_floor(tmp_path):
    mgr = ProviderManager(envelope_home=tmp_path)
    profile = mgr.capability_profile("ollama", "llama3.1")
    assert profile.source == "default"
    assert profile.honest_context == 4096


def test_stored_measured_profile_is_loaded(tmp_path):
    _save(tmp_path, "ollama", "llama3.1", **MEASURED)
    mgr = ProviderManager(envelope_home=tmp_path)
    profile = mgr.capability_profile("ollama", "llama3.1")
    assert profile.source == "probed"
    assert profile.honest_context == 20000
    assert mgr.measured_context_window("ollama", "llama3.1") == 20000


def test_capability_profile_never_raises(tmp_path):
    # Corrupt store file -> floor (load_profile quarantines, answers None).
    path = store.profile_path(tmp_path, "ollama", "bad")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.capability_profile("ollama", "bad").source == "default"
    # envelope_home pointing at a FILE (stat on children fails) -> floor.
    stray = tmp_path / "not-a-dir"
    stray.write_text("x", encoding="utf-8")
    mgr2 = ProviderManager(envelope_home=stray)
    assert mgr2.capability_profile("ollama", "m").source == "default"
    assert mgr2.measured_context_window("ollama", "m") is None


# --------------------------------------------------------------------------- #
# measured_context_window: only probed/partial/tuned WITH a stamp may speak
# --------------------------------------------------------------------------- #


def test_measured_window_speaks_for_partial_and_tuned(tmp_path):
    _save(tmp_path, "ollama", "p", source="partial",
          probed_at="2026-08-22T10:00:00Z", honest_context=12_000,
          measured_fields=["honest_context"])
    _save(tmp_path, "ollama", "t", source="tuned",
          probed_at="2026-08-22T10:00:00Z", honest_context=9_000,
          measured_fields=["honest_context"])
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.measured_context_window("ollama", "p") == 12_000
    assert mgr.measured_context_window("ollama", "t") == 9_000


def test_quick_battery_profile_never_speaks_a_window(tmp_path):
    """THE WAVE-A SHIP-BLOCKER'S PIN AT THIS LAYER: source="probed" with a
    real probed_at stamp, but honest_context NOT in measured_fields — the
    exact profile one Measure click writes today. Before the per-field gate,
    is_measured() alone let this speak the 4096 floor as a measured window
    and shrink a 128k model's context. It must say nothing."""
    _save(tmp_path, "ollama", "big128k", **QUICK_BATTERY)
    mgr = ProviderManager(envelope_home=tmp_path)
    profile = mgr.capability_profile("ollama", "big128k")
    assert profile.is_measured()  # the battery DID run...
    assert not profile.field_measured("honest_context")  # ...but not on this
    assert mgr.measured_context_window("ollama", "big128k") is None
    # And the window ladder is untouched: pin, fleet probe, and None all
    # answer exactly as they would with no envelope at all.
    d = _d(pins={"ollama::big128k": 131_072}, providers=mgr)
    assert _context_window(d, "ollama", "big128k") == 131_072
    d2 = _d(providers=mgr, fleet=_fleet("big128k", 131_072))
    assert _context_window(d2, "ollama", "big128k") == 131_072
    assert _context_window(_d(providers=mgr), "ollama", "big128k") is None


def test_seeded_profile_is_silent(tmp_path):
    _save(tmp_path, "ollama", "s", source="seeded", honest_context=30_000)
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.measured_context_window("ollama", "s") is None


def test_probe_failed_and_stampless_profiles_are_silent(tmp_path):
    _save(tmp_path, "ollama", "pf", source="probe_failed", honest_context=30_000,
          measured_fields=["honest_context"])
    # source claims probed and even names the field, but carries no stamp:
    # is_measured() says no — both gates must hold, not either.
    _save(tmp_path, "ollama", "nostamp", source="probed", honest_context=30_000,
          measured_fields=["honest_context"])
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.measured_context_window("ollama", "pf") is None
    assert mgr.measured_context_window("ollama", "nostamp") is None


# --------------------------------------------------------------------------- #
# _context_window: the ladder (pin > MEASURED envelope > fleet probe > None)
# --------------------------------------------------------------------------- #


def test_pin_beats_envelope(tmp_path):
    _save(tmp_path, "ollama", "llama3.1", **MEASURED)
    mgr = ProviderManager(envelope_home=tmp_path)
    d = _d(pins={"ollama::llama3.1": 8192}, providers=mgr, fleet=_fleet("llama3.1"))
    assert _context_window(d, "ollama", "llama3.1") == 8192


def test_measured_envelope_beats_probe_and_default(tmp_path):
    _save(tmp_path, "ollama", "llama3.1", **MEASURED)
    mgr = ProviderManager(envelope_home=tmp_path)
    # Beats the fleet probe's 32768...
    d = _d(providers=mgr, fleet=_fleet("llama3.1", 32_768))
    assert _context_window(d, "ollama", "llama3.1") == 20_000
    # ...and the None-default (no fleet at all).
    d2 = _d(providers=mgr)
    assert _context_window(d2, "ollama", "llama3.1") == 20_000


def test_seeded_profile_does_not_alter_the_window(tmp_path):
    _save(tmp_path, "ollama", "llama3.1", source="seeded", honest_context=30_000)
    mgr = ProviderManager(envelope_home=tmp_path)
    d = _d(providers=mgr, fleet=_fleet("llama3.1", 32_768))
    assert _context_window(d, "ollama", "llama3.1") == 32_768
    assert _context_window(_d(providers=mgr), "ollama", "llama3.1") is None


def test_trusted_profile_does_not_alter_the_window(tmp_path):
    mgr = ProviderManager(envelope_home=tmp_path)
    # trusted_profile carries honest_context=4096 but is NOT a window
    # authority: with no pin/probe the answer stays None, not 4096.
    assert _context_window(_d(providers=mgr), "anthropic", "claude-opus-4-8") is None
    d = _d(pins={"anthropic": 200_000}, providers=mgr)
    assert _context_window(d, "anthropic", "claude-opus-4-8") == 200_000


def test_default_profile_does_not_alter_the_window(tmp_path):
    _save(tmp_path, "ollama", "llama3.1", source="default", honest_context=4096)
    mgr = ProviderManager(envelope_home=tmp_path)
    d = _d(providers=mgr, fleet=_fleet("llama3.1", 32_768))
    assert _context_window(d, "ollama", "llama3.1") == 32_768
    assert _context_window(_d(providers=mgr), "ollama", "llama3.1") is None


def test_no_profile_behavior_is_byte_identical_to_the_old_ladder(tmp_path):
    """Regression pin: with a manager wired but nothing that may speak —
    an EMPTY store, and equally a store holding only a QUICK-BATTERY profile
    (the ship-blocker case: probed stamp, honest_context unmeasured) — every
    rung answers exactly what a providers-less platform (the pre-envelope
    shape) answers: pin, then fleet probe, then None."""
    empty = ProviderManager(envelope_home=tmp_path / "empty")
    quick_home = tmp_path / "quick"
    _save(quick_home, "ollama", "m", **QUICK_BATTERY)
    quick = ProviderManager(envelope_home=quick_home)
    scenarios = (
        (dict(pins={"ollama::m": 8192}, fleet=_fleet("m")), 8192),   # pin wins
        (dict(pins={"m": 7000}, fleet=_fleet("m")), 7000),           # model pin
        (dict(pins={"ollama": 6000}), 6000),                         # provider pin
        (dict(fleet=_fleet("m", 32_768)), 32_768),                   # fleet probe
        (dict(), None),                                              # unknown
    )
    for kw, expected in scenarios:
        with_empty = _context_window(_d(providers=empty, **kw), "ollama", "m")
        with_quick = _context_window(_d(providers=quick, **kw), "ollama", "m")
        without = _context_window(_d(providers=None, **kw), "ollama", "m")
        assert with_empty == with_quick == without == expected, (kw, expected)


# --------------------------------------------------------------------------- #
# Cache: reused while the store file is unchanged, invalidated when it changes
# --------------------------------------------------------------------------- #


def test_cache_hits_while_unchanged_and_invalidates_on_store_change(tmp_path, monkeypatch):
    _save(tmp_path, "ollama", "llama3.1", **MEASURED)
    mgr = ProviderManager(envelope_home=tmp_path)

    calls: list[tuple[str, str]] = []
    real_load = store.load_profile

    def counting(home, provider, model_id):
        calls.append((provider, model_id))
        return real_load(home, provider, model_id)

    monkeypatch.setattr(manager_mod.envelope_store, "load_profile", counting)

    assert mgr.capability_profile("ollama", "llama3.1").honest_context == 20_000
    assert mgr.capability_profile("ollama", "llama3.1").honest_context == 20_000
    assert len(calls) == 1  # second read served from cache, no re-parse

    # The store file changes (a probe completed) -> next read sees it.
    _save(tmp_path, "ollama", "llama3.1", source="tuned",
          probed_at="2026-08-22T11:00:00Z", honest_context=111_111,
          measured_fields=["honest_context"])
    assert mgr.capability_profile("ollama", "llama3.1").honest_context == 111_111
    assert len(calls) == 2
    assert mgr.measured_context_window("ollama", "llama3.1") == 111_111


def test_cache_invalidates_when_the_profile_file_appears_or_disappears(tmp_path):
    mgr = ProviderManager(envelope_home=tmp_path)
    assert mgr.capability_profile("ollama", "m").source == "default"
    _save(tmp_path, "ollama", "m", **MEASURED)  # file APPEARS after a floor read
    assert mgr.capability_profile("ollama", "m").source == "probed"
    store.profile_path(tmp_path, "ollama", "m").unlink()  # and DISAPPEARS
    assert mgr.capability_profile("ollama", "m").source == "default"
