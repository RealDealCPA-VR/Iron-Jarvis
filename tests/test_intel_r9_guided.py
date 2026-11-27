"""The intelligence plan, release 9: a model that keeps stumbling is guided.

Every tool-carrying reply of a user's own model records whether it came back
in a usable form. Five unusable replies running move the model to the guided
form; ten usable ones running at the guided form move it back. A hard task
alone (step outcomes, goal evidence) never lowers a capable model.
Read through ``ProviderManager`` — the consult every router call and run makes.
"""

from __future__ import annotations

import json

from iron_jarvis.envelope.outcomes import (
    MOVED_BACK,
    MOVED_TO_GUIDED,
    RECOVER_RUN,
    STUMBLE_RUN,
    OutcomeLedger,
    record_outcome,
)
from iron_jarvis.envelope.profile import CURRENT_PROBE_GENERATION, CapabilityProfile
from iron_jarvis.envelope.store import save_profile
from iron_jarvis.providers.manager import ProviderManager

M = "qwen3:30b"


def _capable(**kw) -> CapabilityProfile:
    base = dict(
        model_id=M, provider="ollama", source="probed", probed_at="2026-11-27T00:00:00+00:00",
        tool_protocols={"native": 0.97, "strict_json": 0.95}, json_adherence=0.96,
        probe_generation=CURRENT_PROBE_GENERATION,
        measured_fields=["tool_protocols.native", "tool_protocols.strict_json", "json_adherence"],
    )
    base.update(kw)
    return CapabilityProfile(**base)


def _mgr(tmp_path) -> ProviderManager:
    return ProviderManager(envelope_home=tmp_path)


def test_five_unusable_replies_running_move_a_model_to_the_guided_form(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    assert pm.capability_profile("ollama", M).select_tool_protocol() == "native"
    moved = [pm.note_reply_form("ollama", M, "native", False) for _ in range(STUMBLE_RUN)]
    assert moved == [""] * (STUMBLE_RUN - 1) + [MOVED_TO_GUIDED]
    assert pm.reply_guided("ollama", M) is True
    tuned = pm.capability_profile("ollama", M)
    assert tuned.select_tool_protocol() == "strict_json"
    assert tuned.source == "tuned" and tuned.probed_at == _capable().probed_at


def test_a_usable_reply_breaks_the_run(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for ok in [False] * (STUMBLE_RUN - 1) + [True] + [False] * (STUMBLE_RUN - 1):
        pm.note_reply_form("ollama", M, "native", ok)
    assert pm.reply_guided("ollama", M) is False
    assert pm.capability_profile("ollama", M).select_tool_protocol() == "native"


def test_ten_usable_replies_running_at_the_guided_form_move_it_back(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("ollama", M, "native", False)
    # A reply at the rung it stumbled on does not count toward coming back.
    for _ in range(RECOVER_RUN):
        pm.note_reply_form("ollama", M, "native", True)
    assert pm.reply_guided("ollama", M) is True
    # One unusable guided reply restarts the count.
    for ok in [True] * (RECOVER_RUN - 1) + [False]:
        pm.note_reply_form("ollama", M, "strict_json", ok)
    assert pm.reply_guided("ollama", M) is True
    moved = [pm.note_reply_form("ollama", M, "strict_json", True) for _ in range(RECOVER_RUN)]
    assert moved[-1] == MOVED_BACK and set(moved[:-1]) == {""}
    assert pm.reply_guided("ollama", M) is False
    back = pm.capability_profile("ollama", M)
    assert back.select_tool_protocol() == "native" and back.source == "probed"


def test_a_hard_task_alone_does_not_lower_a_capable_model(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for _ in range(60):
        # Every step of a hard task failed its check — while every reply came
        # back in a usable form.
        record_outcome(tmp_path, "ollama", M, False)
        pm.note_reply_form("ollama", M, "native", True)
    assert pm.reply_guided("ollama", M) is False
    profile = pm.capability_profile("ollama", M)
    assert profile.select_tool_protocol() == "native"
    assert profile.source == "probed" and profile.max_tools() is None
    ledger = OutcomeLedger.load(tmp_path, "ollama", M)
    assert ledger.steps.failures == 60


def test_a_model_nobody_measured_can_be_guided_too(tmp_path):
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("ollama", "small:3b", "native", False)
    assert pm.reply_guided("ollama", "small:3b") is True
    # Its (floor) profile is not a measurement, so nothing is "tuned".
    assert pm.capability_profile("ollama", "small:3b").source == "default"


def test_a_new_measurement_voids_the_stumble(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("ollama", M, "native", False)
    assert pm.reply_guided("ollama", M)
    save_profile(tmp_path, _capable(probed_at="2026-11-28T00:00:00+00:00"))
    assert pm.reply_guided("ollama", M) is False
    assert pm.capability_profile("ollama", M).select_tool_protocol() == "native"


def test_trusted_providers_record_nothing_and_are_never_guided(tmp_path):
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN * 2):
        assert pm.note_reply_form("anthropic", "claude-x", "native", False) == ""
    assert pm.reply_guided("anthropic", "claude-x") is False
    assert not list(tmp_path.glob("envelopes/*.outcomes.json"))


def test_the_fenced_form_alone_never_guides(tmp_path):
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN * 2):
        pm.note_reply_form("ollama", M, "prompted", False)
    assert pm.reply_guided("ollama", M) is False
    raw = json.loads(OutcomeLedger.path_for(tmp_path, "ollama", M).read_text())
    assert raw["guided"] is False and raw["fail_run"] == 0


def test_a_version_one_sidecar_is_goal_evidence_and_is_dropped(tmp_path):
    save_profile(tmp_path, _capable())
    path = OutcomeLedger.path_for(tmp_path, "ollama", M)
    path.write_text(json.dumps({
        "model_id": M, "provider": "ollama", "version": 1,
        "profile_stamp": "x", "tool_protocols": {"native": {"attempts": 50, "failures": 40}},
        "guided": True, "guided_rung": "native",
    }))
    ledger = OutcomeLedger.load(tmp_path, "ollama", M)
    assert ledger.tool_protocols == {} and ledger.guided is False


def test_the_guided_state_survives_a_restart(tmp_path):
    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("ollama", M, "native", False)
    after = _mgr(tmp_path)
    assert after.load_guided_states() == 1
    assert after.reply_guided("ollama", M) is True


def test_reading_the_guided_state_reads_no_ledger_file(tmp_path, monkeypatch):
    """Release 9 review: ``reply_guided`` runs on the event loop after every
    tool reply; the state is kept in memory, never read from the ledger."""
    from iron_jarvis.envelope import outcomes as O

    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN):
        pm.note_reply_form("ollama", M, "native", False)
    reads: list = []
    real = O.OutcomeLedger.load

    def counting(*a, **k):
        reads.append(a)
        return real(*a, **k)

    monkeypatch.setattr(O.OutcomeLedger, "load", counting)
    for _ in range(5):
        assert pm.reply_guided("ollama", M) is True
        assert pm.capability_profile("ollama", M).select_tool_protocol() == "strict_json"
    assert reads == []


def test_replies_finishing_together_never_lose_a_count(tmp_path):
    import threading

    save_profile(tmp_path, _capable())
    pm = _mgr(tmp_path)
    start = threading.Barrier(8)

    def one():
        start.wait()
        for _ in range(10):
            pm.note_reply_form("ollama", M, "native", True)

    threads = [threading.Thread(target=one) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert OutcomeLedger.load(tmp_path, "ollama", M).tool_protocols["native"].attempts == 80


def test_only_your_own_model_servers_are_recorded_or_guided(tmp_path):
    """CI finding on 19ee7cc03 (test_attended_steps_v1402): a test double
    provider ("stepfake") that is neither trusted nor a local server was
    recorded, guided after five calls to a tool it was not given, and its
    replies went through the fenced form. Only Ollama, the custom slot and
    fleet nodes are the user's own models."""
    pm = _mgr(tmp_path)
    for _ in range(STUMBLE_RUN * 2):
        assert pm.note_reply_form("stepfake", "stepfake-1", "native", False) == ""
    assert pm.reply_guided("stepfake", "stepfake-1") is False
    assert not list(tmp_path.glob("envelopes/*.outcomes.json"))
    assert pm.is_own_model_provider("fleet-box") and pm.is_own_model_provider("custom")
    assert not pm.is_own_model_provider("claude-cli") and not pm.is_own_model_provider("stepfake")
