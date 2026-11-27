"""The intelligence plan, release 9: your own models set themselves up.

The first time a model of the user's own is used, it is tried while the PC is
idle: one model at a time, cut the moment the user types, never a cloud model
and never a "local" address that passes a name on to one. Every reading is
injected; nothing here sleeps or reads this PC's real idle time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.routes import envelope as envelope_routes
from iron_jarvis.envelope import autotrial as T
from iron_jarvis.providers.manager import ProviderManager


class PC:
    """The injected readings: idle seconds, busy, Emergency stop."""

    def __init__(self, idle: float | None = 900.0) -> None:
        self.idle = idle
        self.busy = False
        self.stopped = False
        self.during: list[float | None] = []  # idle readings once a trial runs
        self.in_trial = False

    def idle_seconds(self):
        if self.in_trial and self.during:
            return self.during.pop(0)
        return self.idle


def _trials(tmp_path: Path, pc: PC, *, ran: list, refusal=lambda p, m: "", measured=None,
            run=None, fingerprint=None) -> T.AutoTrials:
    async def _run(provider, model):
        ran.append((provider, model))

    if measured is None:
        # A model is measured once its trial really ran (the stand-in battery
        # lands a measurement).
        def measured(p, m):
            return (p, m) in ran

    return T.AutoTrials(
        home=tmp_path,
        is_trusted=lambda p: p in ("anthropic", "openai", "claude-cli"),
        measured=measured,
        refusal=refusal,
        run=run or _run,
        idle_seconds=pc.idle_seconds,
        busy=lambda: pc.busy,
        stopped=lambda: pc.stopped,
        fingerprint=fingerprint or (lambda p, m: "same"),
        watch=0.0,
    )


def step(trials: T.AutoTrials) -> str:
    # A bound, never a wait: a passing step returns at once; a trial that is
    # never cut fails here instead of hanging the suite.
    return asyncio.run(asyncio.wait_for(trials.step(), 10))


def test_only_a_model_of_your_own_is_noted_and_only_once(tmp_path):
    trials = _trials(tmp_path, PC(), ran=[])
    for _ in range(3):
        trials.note_used("ollama", "qwen3:8b")
    trials.note_used("anthropic", "claude-x")
    trials.note_used("mock", "demo")
    trials.note_used("ollama", "")
    assert trials.pending() == [("ollama", "qwen3:8b")]


@pytest.mark.parametrize("idle", [10.0, None])
def test_nothing_is_tried_while_the_pc_is_in_use_or_idle_cannot_be_told(tmp_path, idle):
    ran: list = []
    trials = _trials(tmp_path, PC(idle=idle), ran=ran)
    trials.note_used("ollama", "qwen3:8b")
    assert step(trials) == T.NOT_IDLE
    assert ran == [] and trials.pending() == [("ollama", "qwen3:8b")]


def test_nothing_is_tried_while_a_reply_is_written_or_emergency_stop_is_on(tmp_path):
    ran: list = []
    pc = PC()
    trials = _trials(tmp_path, pc, ran=ran)
    trials.note_used("ollama", "qwen3:8b")
    pc.busy = True
    assert step(trials) == T.NOT_IDLE
    pc.busy, pc.stopped = False, True
    assert step(trials) == T.STOPPED
    assert ran == []


def test_idle_tries_one_model_at_a_time_in_the_order_they_were_used(tmp_path):
    ran: list = []
    trials = _trials(tmp_path, PC(), ran=ran)
    trials.note_used("ollama", "first:8b")
    trials.note_used("fleet-box", "second:30b")
    assert step(trials) == T.RAN
    assert ran == [("ollama", "first:8b")]
    assert step(trials) == T.RAN
    assert ran == [("ollama", "first:8b"), ("fleet-box", "second:30b")]
    assert step(trials) == T.NOTHING
    assert trials.state_of("ollama", "first:8b") == T.DONE


def test_typing_cuts_a_trial_at_once_and_three_cuts_leave_it_for_measure(tmp_path):
    cancelled: list = []

    pc = PC()

    async def never_ends(provider, model):
        pc.in_trial = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(model)
            raise
        finally:
            pc.in_trial = False

    trials = _trials(tmp_path, pc, ran=[], run=never_ends)
    trials.note_used("ollama", "qwen3:8b")
    for n in range(1, T.MAX_CUTS + 1):
        pc.during = [2.0]  # the user pressed a key two seconds ago
        assert step(trials) == T.CUT
        assert len(cancelled) == n and trials.running is None
    assert trials.state_of("ollama", "qwen3:8b") == T.LEFT
    assert step(trials) == T.NOTHING


def test_a_reply_starting_mid_trial_cuts_it_too(tmp_path):
    pc = PC()
    calls = {"n": 0}

    def busy():
        calls["n"] += 1
        return calls["n"] > 1  # idle at the start, a reply begins during the trial

    async def never_ends(provider, model):
        await asyncio.Event().wait()

    trials = _trials(tmp_path, pc, ran=[], run=never_ends)
    trials.busy = busy
    trials.note_used("ollama", "qwen3:8b")
    assert step(trials) == T.CUT
    assert trials.state_of("ollama", "qwen3:8b") == T.PENDING


def test_a_refused_model_is_never_tried_and_an_unknown_one_waits(tmp_path):
    ran: list = []
    answers = {"cloudy": "its model server passes it on to a cloud service", "unread": T.WAIT}
    trials = _trials(tmp_path, PC(), ran=ran, refusal=lambda p, m: answers.get(m, ""))
    trials.note_used("custom", "cloudy")
    trials.note_used("custom", "unread")
    assert step(trials) == T.REFUSED
    assert step(trials) == T.WAITING
    assert ran == []
    assert trials.state_of("custom", "cloudy") == T.LEFT
    assert trials.pending() == [("custom", "unread")]
    answers["unread"] = ""  # the sampler read it: it answers itself
    assert step(trials) == T.RAN and ran == [("custom", "unread")]


def test_a_model_measure_already_measured_is_not_tried_again(tmp_path):
    ran: list = []
    trials = _trials(tmp_path, PC(), ran=ran, measured=lambda p, m: True)
    trials.note_used("ollama", "qwen3:8b")
    assert step(trials) == T.ALREADY and ran == []


def test_what_was_tried_survives_a_restart(tmp_path):
    ran: list = []
    trials = _trials(tmp_path, PC(), ran=ran)
    trials.note_used("ollama", "a:8b")
    trials.note_used("ollama", "b:8b")
    step(trials)
    again = _trials(tmp_path, PC(), ran=ran)
    asyncio.run(asyncio.to_thread(again._load))
    assert again.state_of("ollama", "a:8b") == T.DONE
    assert again.pending() == [("ollama", "b:8b")]
    again.note_used("ollama", "a:8b")  # used again: still done, not tried twice
    assert again.pending() == [("ollama", "b:8b")]


def test_the_lifespan_loop_tries_nothing_in_the_suite(tmp_path):
    ran: list = []
    trials = _trials(tmp_path, PC(), ran=ran)
    trials.note_used("ollama", "qwen3:8b")
    stops: list = []

    async def stop_after_one(_s):
        stops.append(1)
        raise asyncio.CancelledError

    trials.sleep = stop_after_one
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(trials.run_forever())
    assert T._FORCED_OFF is True and ran == [] and stops == [1]


# --------------------------------------------------------------------------- #
# Which models the idle trial may try (routes/envelope.trial_refusal)
# --------------------------------------------------------------------------- #
def _d(*, custom: str = "", ollama: str = "", place=None, plain: bool = False):
    pm = ProviderManager(upstream_place=lambda p, m: place)
    pm.configure_local(
        custom_base_url=custom or None, custom_model="m" if custom else None,
        ollama_base_url=ollama or None, ollama_model="m" if ollama else None,
    )
    config = SimpleNamespace(custom_base_url=custom, ollama_base_url=ollama)
    fleet = SimpleNamespace(answers_itself=lambda provider: plain, get=lambda _id: None, seeded=lambda: [])
    return SimpleNamespace(platform=SimpleNamespace(providers=pm, config=config, fleet=fleet), fleet=fleet)


def test_a_cloud_model_is_never_tried():
    d = _d(ollama="http://127.0.0.1:11434", plain=True)
    assert envelope_routes.trial_refusal(d, "anthropic", "claude-sonnet") == envelope_routes.NEVER_CLOUD
    assert envelope_routes.trial_refusal(d, "ollama", "gpt-oss:120b-cloud") == envelope_routes.NEVER_CLOUD
    assert envelope_routes.trial_refusal(d, "ollama", "gpt-oss:20b") == ""


def test_a_local_address_that_passes_the_name_to_a_cloud_service_is_never_tried():
    told = _d(custom="http://192.168.1.20:4000", place="cloud")
    assert envelope_routes.trial_refusal(told, "custom", "frontier") == envelope_routes.NEVER_FORWARDED
    public = _d(custom="https://api.example-cloud.test/v1", plain=True)
    assert envelope_routes.trial_refusal(public, "custom", "m") == envelope_routes.NEVER_FORWARDED
    # A proxy we could not read is never asked a model in its maker's name.
    for name in ("openai/gpt-4o", "claude-sonnet-4", "gemini-2.5-pro", "gpt-4.1-mini"):
        unread = _d(custom="http://192.168.1.20:4000", plain=True)
        assert envelope_routes.trial_refusal(unread, "custom", name) == envelope_routes.NEVER_CLOUD


def test_a_local_server_waits_until_its_routing_is_known():
    unknown = _d(custom="http://192.168.1.20:4000")
    assert envelope_routes.trial_refusal(unknown, "custom", "qwen3:30b") == T.WAIT
    said_local = _d(custom="http://192.168.1.20:4000", place="local")
    assert envelope_routes.trial_refusal(said_local, "custom", "qwen3:30b") == ""
    answers_itself = _d(custom="http://192.168.1.20:4000", plain=True)
    assert envelope_routes.trial_refusal(answers_itself, "custom", "qwen3:30b") == ""
    no_address = _d()
    assert envelope_routes.trial_refusal(no_address, "custom", "qwen3:30b") == envelope_routes.NEVER_NO_ADDRESS


def test_the_router_notes_a_model_the_first_time_it_answers(tmp_path):
    from iron_jarvis.providers import router as R

    trials = _trials(tmp_path, PC(), ran=[])
    T.install(trials)
    try:
        R._note_call(SimpleNamespace(provider="ollama", model="qwen3:8b"), SimpleNamespace(usage={}))
        R._note_call(SimpleNamespace(provider="openai", model="gpt-x"), SimpleNamespace(usage={}))
    finally:
        T.install(None)
    assert trials.pending() == [("ollama", "qwen3:8b")]


def test_the_sampler_reading_a_plain_server_is_what_answers_itself_reads(tmp_path):
    from iron_jarvis.fleet.registry import FleetRegistry
    from iron_jarvis.fleet.upstreams import NOT_A_PROXY

    config = SimpleNamespace(
        home=str(tmp_path), ollama_base_url="", custom_base_url="http://192.168.1.20:1234",
        custom_model="m", fleet_nodes_json="", custom_api_key_name="",
    )
    try:
        reg = FleetRegistry(config, persist=lambda *a, **k: None)
    except TypeError:
        pytest.skip("registry signature differs")
    nodes = reg._top_level()
    if "custom" not in nodes:
        pytest.skip("no seeded custom node in this build")
    assert reg.answers_itself("custom") is False
    reg.set_routes("custom", NOT_A_PROXY)
    # Release 9 review: "not LiteLLM" is not "answers itself". The custom slot
    # was never DETECTED as Ollama or vLLM, so its models wait (Measure stays).
    assert reg.answers_itself("custom") is False


def test_a_trial_that_measured_nothing_is_tried_again_never_done(tmp_path):
    """Release 9 review: an asleep model server's trial lands nothing; it is
    counted like a cut and tried again, never marked done for ever."""
    tried: list = []
    trials = _trials(tmp_path, PC(), ran=tried, measured=lambda p, m: False)
    trials.note_used("fleet-spark", "glm:30b")
    for n in range(1, T.MAX_CUTS):
        assert step(trials) == T.FAILED
        assert trials.state_of("fleet-spark", "glm:30b") == T.PENDING
    assert step(trials) == T.FAILED
    assert trials.state_of("fleet-spark", "glm:30b") == T.LEFT
    assert len(tried) == T.MAX_CUTS


def test_emergency_stop_mid_trial_is_not_a_cut(tmp_path):
    pc = PC()

    async def never_ends(provider, model):
        pc.stopped = True  # Emergency stop pressed while it runs
        await asyncio.Event().wait()

    trials = _trials(tmp_path, pc, ran=[], run=never_ends)
    trials.note_used("ollama", "qwen3:8b")
    assert step(trials) == T.STOPPED
    assert trials.state_of("ollama", "qwen3:8b") == T.PENDING
    assert trials._models[trials._key("ollama", "qwen3:8b")].cuts == 0


def test_a_model_left_alone_is_looked_at_again_when_its_server_changes(tmp_path):
    ran: list = []
    where = {"fp": "192.168.1.20|7|False|True"}
    verdict = {"why": "its model server passes it on to a cloud service"}
    trials = _trials(tmp_path, PC(), ran=ran, refusal=lambda p, m: verdict["why"],
                     fingerprint=lambda p, m: where["fp"])
    trials.note_used("custom", "qwen3:30b")
    assert step(trials) == T.REFUSED
    assert step(trials) == T.NOTHING  # left alone while nothing changed
    where["fp"], verdict["why"] = "192.168.1.20|8|True|False", ""
    assert step(trials) == T.RAN and ran == [("custom", "qwen3:30b")]


def test_a_proxy_that_can_reach_the_cloud_is_never_tried_unasked():
    d = _d(custom="http://192.168.1.20:4000", place="local")
    d.fleet.table_holds_cloud = lambda provider: True
    assert envelope_routes.trial_refusal(d, "custom", "qwen3:30b") == envelope_routes.NEVER_FORWARDED
    d.fleet.table_holds_cloud = lambda provider: False
    assert envelope_routes.trial_refusal(d, "custom", "qwen3:30b") == ""


def test_any_server_not_positively_known_waits_whatever_its_names():
    """The reviewer's probe: a non-LiteLLM proxy at a private address read as
    "not a proxy" answered "" for every name. Now only a node a probe
    DETECTED as Ollama or vLLM may answer itself (FleetRegistry.answers_itself);
    the stand-in fleet below says no, so every name waits."""
    d = _d(custom="http://192.168.1.20:3000", plain=False)
    for name in ("meta-llama/llama-3.3-70b-instruct", "moonshotai/kimi-k2", "qwen/qwen3-max",
                 "z-ai/glm-4.6", "sonnet", "opus", "haiku", "frontier", "fleet"):
        assert envelope_routes.trial_refusal(d, "custom", name) == T.WAIT, name


def _registry(tmp_path, **node):
    from iron_jarvis.fleet.registry import FleetRegistry

    config = SimpleNamespace(
        home=str(tmp_path), ollama_base_url="", custom_base_url="", custom_model="",
        fleet_nodes=[{"id": "box", "label": "Box", "base_url": "http://192.168.1.20:8000",
                      "routable": True, **node}],
    )
    return FleetRegistry(config, persist=lambda *a, **k: None)


def test_answers_itself_only_for_a_detected_ollama_or_vllm_speaking_openai(tmp_path):
    from iron_jarvis.fleet.upstreams import NOT_A_PROXY

    cases = [
        ({"kind": "vllm", "kind_detected_at": 1.0}, True),
        ({"kind": "ollama", "kind_detected_at": 1.0}, True),
        ({"kind": "vllm", "kind_detected_at": 0.0}, False),  # a guess, not detected
        ({"kind": "openai-compat", "kind_detected_at": 1.0}, False),  # OpenWebUI, one-api...
        ({"kind": "unknown", "kind_detected_at": 1.0}, False),
        ({"kind": "vllm", "kind_detected_at": 1.0, "protocol": "anthropic"}, False),
    ]
    for node, want in cases:
        reg = _registry(tmp_path, **node)
        reg.set_routes("box", NOT_A_PROXY)
        assert reg.answers_itself("fleet-box") is want, node


def test_an_unread_pass_forgets_answers_itself(tmp_path):
    from iron_jarvis.fleet.upstreams import NOT_A_PROXY, UNREAD

    reg = _registry(tmp_path, kind="vllm", kind_detected_at=1.0)
    reg.set_routes("box", NOT_A_PROXY)
    assert reg.answers_itself("fleet-box") is True
    reg.set_routes("box", UNREAD)  # replaced by a keyed proxy that now says 401
    assert reg.answers_itself("fleet-box") is False


def test_a_table_holding_any_cloud_entry_is_said(tmp_path):
    from iron_jarvis.fleet.upstreams import RouteTable, Upstream

    reg = _registry(tmp_path, kind="litellm", kind_detected_at=1.0)
    assert reg.table_holds_cloud("fleet-box") is None
    reg.set_routes("box", RouteTable(entries=(Upstream("glm", "hosted_vllm/glm", "local"),)))
    assert reg.table_holds_cloud("fleet-box") is False
    reg.set_routes("box", RouteTable(entries=(
        Upstream("glm", "hosted_vllm/glm", "local"), Upstream("frontier", "openrouter/x", "cloud"),
    )))
    assert reg.table_holds_cloud("fleet-box") is True


def test_a_model_tried_by_itself_sends_no_notice_even_to_a_subscribed_phone():
    from iron_jarvis.comm.channels import MockChannel
    from iron_jarvis.comm.notifier import Notifier

    n = Notifier(event_types={"envelope.probe_completed"})
    phone = MockChannel()
    n.add_channel("phone", phone)
    payload = {"provider": "ollama", "model": "qwen3:8b", "source": "probed"}
    n.on_event({"type": "envelope.probe_completed", "session_id": None, "payload": {**payload, "auto": True}})
    assert phone.sent == []
    # The user's own Measure press still reaches what they subscribed to.
    n.on_event({"type": "envelope.probe_completed", "session_id": None, "payload": payload})
    assert len(phone.sent) == 1
