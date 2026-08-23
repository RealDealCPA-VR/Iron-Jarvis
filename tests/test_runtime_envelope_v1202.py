"""v1.202.0 Wave B — the AGENT lane bends under a MEASURED capability envelope.

Three consults, one narration, and a hard zero-change floor:

* B1 — ``arm_for_task`` takes the envelope's ``max_tools()`` as an arming
  budget with the same contract ``tools/autoselect.select_auto_tools`` gives
  its ``max_tools`` parameter: the roster is CONSENT and is never dropped,
  only the auto additions shrink, and ``None`` (trusted/unmeasured) is
  byte-identical to v1.201.0.
* B2 — ``should_decompose_enveloped`` wraps (never edits) the
  ``decompose.should_decompose`` gate: a measured, untrusted profile whose
  ``needs_decomposition()`` answers True routes a multi-step task through the
  decomposed lane even where today's gate runs flat (the prompted-mode reach
  limit), while ``decompose_local_tasks = False`` silences the envelope
  reason along with the rest. The ``is_measured()`` gate is THE load-bearing
  line: the unmeasured floor profile answers ``needs_decomposition() ==
  True`` by conservative construction, and consulting it raw would flip
  every unprobed local provider into the decomposed lane.
* B4 — ``envelope.adapted`` is published ONCE per run, only when the loop
  actually bent, payload ``{provider, model, adaptations, source}``, tagged
  with the session id, and NEVER for trusted or unmeasured profiles. It is
  published AFTER the decompose decision RESOLVES: deciding to decompose is
  not decomposing — the planner may DECLINE (degenerate/unparseable plan →
  ``run_decomposed`` returns None → flat-loop fallback), and the event must
  then carry the arm-time bends only, or nothing at all when none bit. A run
  with zero REALIZED bends publishes NOTHING (the reviewer's confirmed
  Wave-B defect, pinned below in both directions).
* The supervisor lane: ``run_supervised`` drives ``AgentRuntime.run``, whose
  gate has no agent-type branch — so a SUPERVISOR session with a weak
  measured envelope takes the same decomposed lane (and one test pins the
  structural finding that the lane was already reachable today via the
  prompted reason, WITHOUT an adapted event, because a base-gate
  decomposition is not an envelope bend).

All offline: scripted fake adapters (the test_decompose_v1132 idiom), no
network, no model calls.
"""

from __future__ import annotations

from types import SimpleNamespace

import iron_jarvis.agents.decompose as _dec
from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.agents.runtime import (
    _AUTO_ARM_CAP,
    ENVELOPE_ADAPTED,
    AgentRuntime,
    arm_for_task,
    resolve_run_envelope,
    should_decompose_enveloped,
)
from iron_jarvis.agents.supervisor import run_supervised
from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentState, AgentType
from iron_jarvis.envelope.profile import CapabilityProfile, trusted_profile
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMMessage, LLMResponse

# --------------------------------------------------------------- scripted fakes
class _Native(LLMAdapter):
    """Natively tool-capable adapter — today's gate runs it FLAT (the
    prompted-mode reach limit), which is exactly what makes it the honest
    subject for "the envelope engaged where nothing else would have"."""

    def __init__(self, replies=(), provider="native-x", model="m1"):
        self.provider, self.model = provider, model
        self._replies = list(replies)

    def capabilities(self):
        return {
            "provider": self.provider,
            "model": self.model,
            "tool_use": True,
            "vision": False,
        }

    async def complete(self, *, system, messages, tools):
        return LLMResponse(text=self._replies.pop(0), usage={})


class _TextOnly(_Native):
    """Text-only adapter → the router wraps it prompted → today's gate already
    decomposes multi-step tasks for it, envelope or no envelope."""

    def capabilities(self):
        caps = super().capabilities()
        caps["tool_use"] = False
        return caps


# ------------------------------------------------------------------- profiles
_STAMP = "2026-08-22T00:00:00+00:00"


def _weak(provider="native-x", model="m1") -> CapabilityProfile:
    """A battery RAN and the native rung was lost: max_tools() == 3,
    needs_decomposition() == True. The measured-weak shape all of Wave B
    exists for."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.5, "strict_json": 0.95},
        json_adherence=0.95,
        coherence_horizon=8,
        measured_fields=[
            "tool_protocols.native",
            "tool_protocols.strict_json",
            "json_adherence",
            "chars_per_token",
        ],
    )


def _strong(provider="native-x", model="m1") -> CapabilityProfile:
    """Measured and the model HELD the native rung: the envelope grants the
    full menu and no decomposition — evidence of strength bends nothing."""
    return CapabilityProfile(
        model_id=model,
        provider=provider,
        source="probed",
        probed_at=_STAMP,
        tool_protocols={"native": 0.98, "strict_json": 0.97},
        json_adherence=0.96,
        coherence_horizon=10,
        measured_fields=["tool_protocols.native", "tool_protocols.strict_json"],
    )


def _floor(provider="native-x", model="m1") -> CapabilityProfile:
    """The unmeasured default floor — nothing was ever asked about this model."""
    return CapabilityProfile(model_id=model, provider=provider)


# ------------------------------------------------------------------- fixtures'
# little helpers (the test_agent_auto_arm_v1178 idioms)
_REVIEWER = ["read_file", "list_files", "grep", "read_document", "extract_pdf",
             "memory_search", "skill_search", "recall_lessons", "recall",
             "ltm_search", "blackboard_post", "blackboard_read", "message_agent"]

#: Trips half the rule table (read tier only, on the reviewer) — v1.178.0's
#: proven cap-filler.
_BUSY = (
    "rename every file in the folder to match its contents, summarize the "
    "pdf report, check the spreadsheet formulas, redact the pii, look it up "
    "online and search our history for what we decided"
)

#: Multi-step (two imperative clauses), NOT bulk — on a native adapter today's
#: gate runs this flat, which is the precondition for envelope attribution.
_MULTI = "Read notes.txt then write a summary to out.md"

#: The end-to-end task: > 200 chars (multi-step by length), NOT bulk (no
#: quantifier + collection, no folder scope), and it arms tools the reviewer
#: roster does not carry (web_search, excel_formula_check) so the cap has
#: something real to drop.
_E2E_TASK = (
    "Look up the latest IRS standard mileage rate online and check the "
    "formulas in the quarterly spreadsheet, comparing what the published "
    "source says against the workbook's assumptions, and close with a short "
    "note describing any mismatch you found between the two figures."
)


def _sess(task=_MULTI, provider="native-x", model=None):
    return SimpleNamespace(id="sess-env", task=task, provider=provider, model=model)


def _capture_router(platform, seen: dict):
    """Stand in for the router's stream: record the specs, finalize at once."""

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        seen.setdefault("tools", []).append([s["name"] for s in tools])
        yield {
            "type": "final",
            "response": LLMResponse(text="done", tool_calls=[], usage={}),
            "provider": "mock",
            "model": "mock",
        }

    platform.router.stream = fake_stream
    return seen


async def _never_decomposed(*a, **k):  # pragma: no cover - failure body
    raise AssertionError("run_decomposed must not be reached in this test")


# =============================================================================
# 1. B1 — the arming budget
# =============================================================================
def test_no_envelope_cap_is_byte_identical_and_reports_nothing(platform):
    """max_tools=None (every trusted/unmeasured profile, every pre-envelope
    caller) must be indistinguishable from not passing the parameter — same
    names, same order — and must never touch the adaptations list."""
    for task in (_BUSY, _E2E_TASK, "say hello to the team", ""):
        notes: list[str] = []
        assert arm_for_task(
            platform, task, list(_REVIEWER), max_tools=None, adaptations=notes
        ) == arm_for_task(platform, task, list(_REVIEWER))
        assert notes == []
    # The wiring's no-op guarantee is the PROFILE's, pinned here so a band
    # change cannot silently start capping frontier or unprobed runs:
    assert trusted_profile("openai", "gpt-5.2").max_tools() is None
    assert _floor().max_tools() is None
    assert _strong().max_tools() is None
    assert _weak().max_tools() == 3  # the band every capped test below rides


def test_the_roster_is_consent_and_survives_any_budget(platform):
    """13 explicit grants against a budget of 3: every one survives — skill
    evidence must never override consent — and only the additions go to zero.
    The drop is REPORTED, once, as tool_cap:<n>."""
    notes: list[str] = []
    armed = arm_for_task(platform, _BUSY, list(_REVIEWER), max_tools=3, adaptations=notes)
    assert armed == _REVIEWER
    assert notes == ["tool_cap:3"]


def test_the_cap_truncates_the_same_ranked_slice(platform):
    """A capped selection is the uncapped selection's prefix (autoselect pins
    its own max_tools as "the SAME ranked slice"), applied after the registry
    filter so a tiny budget is never spent on ghost names."""
    base = ["read_file"]
    full = arm_for_task(platform, _BUSY, list(base))
    added_full = full[1:]
    assert len(added_full) >= 3, "the busy task must arm additions to slice"
    notes: list[str] = []
    capped = arm_for_task(platform, _BUSY, list(base), max_tools=3, adaptations=notes)
    assert capped == ["read_file", *added_full[:2]]  # room = 3 - len(roster)
    assert notes == ["tool_cap:3"]


def test_a_cap_that_drops_nothing_reports_nothing(platform):
    """"adapted" means the loop BENT. A budget that never bit — no signal in
    the task, or room for everything selected — must not narrate a bend."""
    notes: list[str] = []
    assert (
        arm_for_task(platform, "say hello", list(_REVIEWER), max_tools=3, adaptations=notes)
        == _REVIEWER
    )
    assert notes == []
    wide: list[str] = []
    assert arm_for_task(
        platform, _BUSY, ["read_file"], max_tools=1 + _AUTO_ARM_CAP + 1, adaptations=wide
    ) == arm_for_task(platform, _BUSY, ["read_file"])
    assert wide == []


# =============================================================================
# 2. B2 — the decompose gate, envelope-consulted
# =============================================================================
def test_envelope_reason_engages_where_todays_gate_runs_flat(platform):
    """The prompted-mode reach limit, crossed on EVIDENCE: a native tool-use
    endpoint runs flat today (pinned first), and a measured-weak envelope
    routes the same session through the decomposed lane — attributed."""
    platform.providers.register("native-x", lambda model=None: _Native())
    assert not _dec.should_decompose(platform, _sess())  # today: flat
    assert should_decompose_enveloped(platform, _sess(), _weak()) == (True, True)


def test_flag_false_is_a_global_off_override_in_both_directions(platform):
    """decompose_local_tasks = False means NEVER — the envelope reason is
    silenced along with the bulk and prompted ones."""
    platform.providers.register("native-x", lambda model=None: _Native())
    platform.config.decompose_local_tasks = False
    assert should_decompose_enveloped(platform, _sess(), _weak()) == (False, False)


def test_the_unmeasured_floor_NEVER_engages(platform):
    """THE load-bearing pin. The floor profile answers needs_decomposition()
    True by conservative construction — asserted here so this test dies with
    any softening of that floor — and the gate still refuses it: the envelope
    bends on EVIDENCE, and an unprobed local provider keeps today's loop
    byte-identical. Deleting the is_measured() gate goes red exactly here."""
    platform.providers.register("native-x", lambda model=None: _Native())
    floor = _floor()
    assert floor.needs_decomposition() is True
    assert should_decompose_enveloped(platform, _sess(), floor) == (False, False)


def test_trusted_and_measured_strong_profiles_never_engage(platform):
    """Frontier sees zero change; and a model that MEASURED strong earned its
    flat loop — evidence of strength is not a reason to decompose."""
    platform.providers.register("native-x", lambda model=None: _Native())
    trusted = trusted_profile("native-x", "m1")
    assert should_decompose_enveloped(platform, _sess(), trusted) == (False, False)
    assert should_decompose_enveloped(platform, _sess(), _strong()) == (False, False)


def test_a_simple_task_stays_flat_on_the_weakest_profile(platform):
    """plan/verify on a one-liner spends a planner round to learn there is
    nothing to plan — same reason the prompted reason requires multi-step."""
    platform.providers.register("native-x", lambda model=None: _Native())
    assert should_decompose_enveloped(
        platform, _sess(task="Say hello"), _weak()
    ) == (False, False)


def test_base_gate_engagement_is_never_attributed_to_the_envelope(platform):
    """A prompted-mode local adapter decomposes multi-step work TODAY. The
    wrapper must engage and say the envelope caused nothing — or the adapted
    event would narrate a bend that was going to happen anyway."""
    platform.providers.register("local-x", lambda model=None: _TextOnly())
    s = _sess(provider="local-x")
    assert _dec.should_decompose(platform, s)  # the base gate on its own
    assert should_decompose_enveloped(platform, s, _weak("local-x", "m1")) == (
        True,
        False,
    )


# =============================================================================
# 3. resolve_run_envelope — how the runtime knows its model
# =============================================================================
def test_resolve_reads_the_adapters_default_model_when_the_session_has_none(platform):
    """A session created without a model rides the adapter's own default,
    which is only truly resolved at complete() time — the consult uses the
    adapter's advertised answering model, the same pre-run resolution
    decompose.resolved_tool_mode performs."""
    platform.providers.register("native-x", lambda model=None: _Native())
    provider, model, prof = resolve_run_envelope(platform, _sess(model=None))
    assert (provider, model) == ("native-x", "m1")
    assert prof.source == "default"  # nothing measured on this install
    assert prof.max_tools() is None and not prof.is_measured()


def test_resolve_follows_the_sessions_stamped_model(platform):
    """create_session stamps every session with a model (the config default
    when the caller picked nothing) — when a stamp exists, the consult reads
    it and never second-guesses it against the adapter."""
    platform.providers.register("native-x", lambda model=None: _Native())
    provider, model, _prof = resolve_run_envelope(platform, _sess(model="qwen3:30b"))
    assert (provider, model) == ("native-x", "qwen3:30b")


def test_resolve_default_route_lands_on_the_trusted_default_provider(platform):
    """An empty provider = the router's default route. On a fresh install that
    is mock, which is trusted BY CONSTRUCTION — zero envelope behavior."""
    provider, _model, prof = resolve_run_envelope(
        platform, SimpleNamespace(id="s", task="t", provider="", model="")
    )
    assert provider == "mock"
    assert prof.is_trusted() and prof.max_tools() is None


def test_resolve_never_raises_on_a_bare_stub():
    """Every resolution failure answers the unmeasured floor, which bends
    nothing — an envelope consult must never be able to break a run."""
    bare = SimpleNamespace(router=SimpleNamespace(), config=SimpleNamespace())
    _p, _m, prof = resolve_run_envelope(bare, SimpleNamespace(provider=None, model=None))
    assert prof.max_tools() is None and not prof.is_measured()


# =============================================================================
# 4. THE SEAM — a real run bends, narrates once, and only on evidence
# =============================================================================
async def test_a_weak_envelope_bends_a_real_run_and_narrates_exactly_once(
    platform, orchestrator, monkeypatch
):
    """End-to-end on the runtime's own call sites: a REVIEWER session on a
    native adapter (flat today) with a measured-weak envelope (1) has its
    additions capped at the specs the decomposed lane actually receives,
    (2) takes the decomposed lane, and (3) publishes ONE envelope.adapted
    event carrying both bends, the session id, and the profile's provenance."""
    platform.providers.register("native-x", lambda model=None: _Native())
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _weak(p, m)
    )
    seen: dict = {}

    async def fake_decomposed(rt, run, sess, adef, *, tool_specs, **kw):
        seen["tools"] = [s["name"] for s in tool_specs]
        return "decomposed done"

    monkeypatch.setattr(_dec, "run_decomposed", fake_decomposed)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    # Model passed EXPLICITLY: create_session stamps config.default_model onto
    # a model-less session, and the consult honestly follows the stamp — the
    # explicit pick is the path where (provider, model) is exact.
    sess = await orchestrator.create_session(
        _E2E_TASK, AgentType.REVIEWER, provider="native-x", model="m1"
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.REVIEWER)
    )

    assert run.state is AgentState.COMPLETED and run.result == "decomposed done"
    # (1) the cap bit at the seam: the task argues for web_search, the budget
    # (3) is below the roster (13), so no addition may reach the model...
    assert "web_search" not in seen["tools"]
    # ...while every explicit grant survived.
    assert set(seen["tools"]) == {
        s["name"]
        for s in platform.registry.specs(get_agent_definition(AgentType.REVIEWER).tools)
    }
    # (3) narrated ONCE, with the exact payload — both bends, arming first.
    adapted = [e for e in events if e.type == ENVELOPE_ADAPTED]
    assert len(adapted) == 1
    assert adapted[0].session_id == sess.id
    assert adapted[0].payload == {
        "provider": "native-x",
        "model": "m1",
        "adaptations": ["tool_cap:3", "decomposed"],
        "source": "probed",
    }


async def test_a_declined_planner_never_claims_a_decomposition(
    platform, orchestrator, monkeypatch
):
    """THE REVIEWER'S CONFIRMED DEFECT, pinned through the REAL run_decomposed
    seam. The envelope engages the lane, the planner answers a VALID degenerate
    plan ('{"steps": []}' — the test_decompose_v1132 decline idiom, no repair
    round), run_decomposed returns None, and the run falls back to the flat
    loop. The one adapted event must carry the arm-time bend ONLY — an event
    published before the planner spoke would permanently claim a decomposition
    that never happened, and moving the publish back above the planner turns
    this pin red."""
    adapter = _Native(['{"steps": []}'])  # the planner one-shot's scripted reply
    platform.providers.register("native-x", lambda model=None: adapter)
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _weak(p, m)
    )
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    seen = _capture_router(platform, {})  # the flat FALLBACK lane's router

    sess = await orchestrator.create_session(
        _E2E_TASK, AgentType.REVIEWER, provider="native-x", model="m1"
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.REVIEWER)
    )

    assert run.state is AgentState.COMPLETED and run.result == "done"
    assert seen["tools"], "the flat fallback lane never ran"
    # No plan was ever created, so no event may say the run decomposed...
    assert not [e for e in events if e.type == EventType.PLAN_CREATED]
    adapted = [e for e in events if e.type == ENVELOPE_ADAPTED]
    assert len(adapted) == 1
    assert adapted[0].payload["adaptations"] == ["tool_cap:3"]
    assert "decomposed" not in adapted[0].payload["adaptations"]


async def test_zero_realized_bends_publish_nothing(
    platform, orchestrator, monkeypatch
):
    """The other half of the fix: a weak envelope whose cap never bit (no auto
    additions to drop) AND whose engagement the planner declined has adapted
    NOTHING — so nothing may be narrated, however weak the model measured."""
    import iron_jarvis.tools.autoselect as _auto

    adapter = _Native(['{"steps": []}'])
    platform.providers.register("native-x", lambda model=None: adapter)
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _weak(p, m)
    )
    monkeypatch.setattr(_auto, "select_auto_tools", lambda *a, **k: [])
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    _capture_router(platform, {})

    sess = await orchestrator.create_session(
        _E2E_TASK, AgentType.REVIEWER, provider="native-x", model="m1"
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.REVIEWER)
    )

    assert run.state is AgentState.COMPLETED
    assert not [e for e in events if e.type == ENVELOPE_ADAPTED]


async def test_an_unmeasured_local_provider_keeps_todays_run_byte_identical(
    platform, orchestrator, monkeypatch
):
    """The frontier/unprobed zero-change pin at the run level: the REAL
    capability_profile answers the floor for an unprobed local provider, the
    floor's needs_decomposition() is True (asserted — this is the trap), and
    still: flat lane, uncapped additions, no adapted event."""
    platform.providers.register("native-x", lambda model=None: _Native())
    prof = platform.providers.capability_profile("native-x", "m1")
    assert not prof.is_measured() and prof.needs_decomposition() is True
    monkeypatch.setattr(_dec, "run_decomposed", _never_decomposed)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    seen = _capture_router(platform, {})

    sess = await orchestrator.create_session(
        _E2E_TASK, AgentType.REVIEWER, provider="native-x"
    )
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.REVIEWER)
    )

    assert run.state is AgentState.COMPLETED
    # Additions arrive UNCAPPED — exactly the v1.201.0 arming.
    assert "web_search" in seen["tools"][0]
    assert not [e for e in events if e.type == ENVELOPE_ADAPTED]


async def test_a_trusted_default_run_sees_zero_envelope_behavior(
    platform, orchestrator, monkeypatch
):
    """The default (mock) provider is trusted by construction: flat lane, no
    cap, no event — the frontier pin on the actual default route."""
    monkeypatch.setattr(_dec, "run_decomposed", _never_decomposed)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))
    seen = _capture_router(platform, {})

    sess = await orchestrator.create_session(_E2E_TASK, AgentType.REVIEWER)
    run = await AgentRuntime(platform).run(
        sess, get_agent_definition(AgentType.REVIEWER)
    )

    assert run.state is AgentState.COMPLETED
    assert "web_search" in seen["tools"][0]
    assert not [e for e in events if e.type == ENVELOPE_ADAPTED]


# =============================================================================
# 5. The SUPERVISOR lane
# =============================================================================
async def test_supervisor_with_weak_envelope_takes_the_decomposed_lane(
    platform, monkeypatch
):
    """The repo's oldest open item, closed at the gate: run_supervised drives
    AgentRuntime.run, the gate has no agent-type branch, so a SUPERVISOR
    session whose measured envelope demands decomposition reaches decompose
    .py's plan/verify engine — with the supervisor's own definition (delegate
    + worklist) riding into the lane — and the bend is narrated."""
    platform.providers.register("native-x", lambda model=None: _Native())
    monkeypatch.setattr(
        platform.providers, "capability_profile", lambda p, m: _weak(p, m)
    )
    called: dict = {}

    async def fake_decomposed(rt, run, sess, adef, *, tool_specs, **kw):
        called["type"] = adef.type
        called["tools"] = list(adef.tools)
        return "supervised, decomposed"

    monkeypatch.setattr(_dec, "run_decomposed", fake_decomposed)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    sess = await Orchestrator(platform).create_session(
        _E2E_TASK, AgentType.SUPERVISOR, provider="native-x"
    )
    run = await run_supervised(platform, sess)

    assert run.state is AgentState.COMPLETED
    assert called["type"] is AgentType.SUPERVISOR
    assert "delegate" in called["tools"]
    adapted = [e for e in events if e.type == ENVELOPE_ADAPTED]
    assert len(adapted) == 1
    assert "decomposed" in adapted[0].payload["adaptations"]


async def test_supervisor_reaches_the_lane_today_via_the_prompted_reason(
    platform, monkeypatch
):
    """The structural finding, pinned: the supervisor lane could ALREADY reach
    run_decomposed (run_supervised → AgentRuntime.run → the shared gate) via
    the prompted-mode reason — and a base-gate decomposition is NOT an
    envelope bend, so no adapted event may narrate it."""
    platform.providers.register("local-x", lambda model=None: _TextOnly())
    called: dict = {}

    async def fake_decomposed(rt, run, sess, adef, **kw):
        called["type"] = adef.type
        return "supervised, decomposed"

    monkeypatch.setattr(_dec, "run_decomposed", fake_decomposed)
    events: list = []
    platform.event_bus.add_handler(lambda e: events.append(e))

    sess = await Orchestrator(platform).create_session(
        _MULTI, AgentType.SUPERVISOR, provider="local-x"
    )
    run = await run_supervised(platform, sess)

    assert run.state is AgentState.COMPLETED
    assert called["type"] is AgentType.SUPERVISOR
    assert not [e for e in events if e.type == ENVELOPE_ADAPTED]
