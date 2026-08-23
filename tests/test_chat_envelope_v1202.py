"""The chat lanes consult the capability envelope when arming (v1.202.0, Wave B).

Contract under test (agreed with the frontend, the doors v1.199.0 pattern):

* BOTH chat lanes resolve the answering model's ``CapabilityProfile`` before
  arming and pass ``profile.max_tools()`` into ``_resolve_armed_tools`` — a
  measured-weak model gets a NARROWER auto-arm ceiling; explicit user picks
  are consent and are NEVER dropped by the cap;
* trusted (cloud/CLI/mock) and unmeasured profiles answer ``None`` and arming
  is byte-identical to pre-envelope behavior (mutation-sensitive pins);
* every turn's payload carries ``"adapted": {"model", "changes":
  ["tool_cap:<n>", ...]} | null`` — ALWAYS PRESENT, null when nothing bent
  (the common case), identical key in the SSE done frame (lane parity);
* "adapted" means the loop BENT, never that a budget existed: the gate is
  ``ArmedSelection.dropped`` — a candidate the ceiling actually excluded —
  so a plain "hello" on a weak model and 5 explicit picks under cap 3 both
  disclose NOTHING (the two Wave-B reviewer repros, pinned in both lanes),
  and ``<n>`` is the ceiling that bit (never below the armed count).

Fully offline; the router monkeypatch harness is the same one
``tests/test_doors_v1199.py`` uses.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _MAX_ARMED_TOOLS, _resolve_armed_tools
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: A request whose sentence reliably auto-arms web tools (the "search" rule
#: in tools/autoselect scores web_search 8 / web_fetch 3).
_WEB_ASK = "search the web for the latest EV tax credit news"

#: Registered tools select_auto_tools would NOT pick for _WEB_ASK — so the
#: explicit and auto sets in these tests can never collide by accident.
_EXPLICIT = ["read_document", "list_files", "image_info"]


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _weak_profile(native: float = 0.5) -> CapabilityProfile:
    """A measured local-model profile whose native tool-form score is real
    battery evidence — the ONLY kind of profile max_tools() caps on."""
    return CapabilityProfile(
        model_id="tiny",
        provider="ollama",
        source="probed",
        probed_at="2026-08-22T00:00:00+00:00",
        tool_protocols={"native": native, "strict_json": 0.6},
        measured_fields=["tool_protocols.native", "tool_protocols.strict_json"],
    )


def _pin_weak_model(client, monkeypatch, profile: CapabilityProfile):
    """capability_profile answers `profile` for (ollama, tiny) ONLY — every
    other route (the mock default) keeps the real trusted/floor answer, so a
    test can hold both a capped and an uncapped turn side by side."""
    providers = client.app.state.platform.providers
    real = providers.capability_profile

    def fake(provider: str, model: str) -> CapabilityProfile:
        if (provider, model) == ("ollama", "tiny"):
            return profile
        return real(provider, model)

    monkeypatch.setattr(providers, "capability_profile", fake)


def _capture_complete(client, monkeypatch, seen: dict):
    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        seen["tools"] = list(tools or [])
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    monkeypatch.setattr(
        client.app.state.platform.router, "complete", fake_complete
    )


def _capture_stream(client, monkeypatch, seen: dict):
    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        seen["tools"] = list(tools or [])
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"),
               "provider": "mock", "model": "mock"}

    monkeypatch.setattr(client.app.state.platform.router, "stream", fake_stream)


def _stream_done(client, payload):
    """The done frame — detected the same way tests/test_doors_v1199.py does
    (only the done frame carries `escalate`)."""
    with client.stream("POST", "/chat/stream", json=payload) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if "escalate" in frame:
                    done = frame
    assert done is not None, "no done frame arrived"
    return done


def _spec_names(specs) -> set[str]:
    return {s.get("name", "") for s in specs}


# --------------------------------------------------------------------------- #
# 1 — the unit: _resolve_armed_tools under a cap
# --------------------------------------------------------------------------- #


def _fake_d():
    registry = SimpleNamespace(get=lambda name: object())
    return SimpleNamespace(platform=SimpleNamespace(registry=registry))


def _body(tools: list[str], question: str = _WEB_ASK):
    return SimpleNamespace(
        tools=tools,
        skill="",
        auto_tools=True,
        attachments=None,
        messages=[SimpleNamespace(role="user", content=question)],
    )


def test_no_cap_is_byte_identical_to_the_pre_envelope_call():
    """MUTATION PIN: max_tools=None must change NOTHING — the default-arg
    call and the explicit-None call answer the same lists, the auto pass
    still fires (web_search arms for the web ask), and the drop signal is
    zero (nothing narrowed the menu, so nothing can have been dropped)."""
    d = _fake_d()
    baseline = _resolve_armed_tools(d, _body(_EXPLICIT[:2]))
    with_none = _resolve_armed_tools(d, _body(_EXPLICIT[:2]), None)
    assert baseline == with_none
    assert baseline.dropped == 0 and with_none.dropped == 0
    armed, auto = baseline
    assert "web_search" in auto
    assert armed[: len(_EXPLICIT[:2])] == _EXPLICIT[:2]  # explicit first


def test_return_keeps_the_pre_envelope_tuple_shape():
    """Four pre-envelope test files unpack `(armed, auto)` and one compares
    `== ([], [])` — the drop signal must ride as attributes on a 2-tuple, not
    as a third slot that breaks every legacy caller."""
    d = _fake_d()
    res = _resolve_armed_tools(d, _body([], question="hello"))
    assert res == ([], [])          # plain-tuple equality, the pinned idiom
    a, b = res                      # 2-way unpack, the pinned shape
    assert a == [] and b == []
    assert res.dropped == 0 and res.ceiling == _MAX_ARMED_TOOLS


def test_cap_narrows_the_auto_slots_but_never_drops_an_explicit_pick():
    d = _fake_d()
    # Three explicit picks + cap 3: zero auto slots remain — but every pick
    # survives (consent; the cap is about the auto menu, not the user's hand).
    # The web candidates WOULD have armed at the standing ceiling, so the
    # drop signal reports them.
    res = _resolve_armed_tools(d, _body(_EXPLICIT), 3)
    armed, auto = res
    assert armed == _EXPLICIT
    assert auto == []
    assert res.dropped >= 1
    assert res.ceiling == 3
    # More picks than the cap: STILL all kept (the ceiling floors at
    # len(explicit)); the one remaining baseline slot held web_search, so
    # exactly one candidate was cut by the envelope.
    five = _EXPLICIT + ["convert_document", "write_document"]
    res5 = _resolve_armed_tools(d, _body(five), 3)
    armed5, auto5 = res5
    assert armed5 == five
    assert auto5 == []
    assert res5.dropped == 1
    assert res5.ceiling == 5  # floored at len(explicit) — the width that bit


def test_reviewer_repro_a_nothing_to_arm_drops_nothing():
    """THE FIRST WAVE-B REVIEWER REPRO, at the unit: a plain "hello" under a
    cap has no auto candidates, so NOTHING was dropped — the disclosure gate
    reads this signal, and a non-zero here would stamp a permanent 'adapted'
    line on every turn of a weak-pinned model."""
    d = _fake_d()
    res = _resolve_armed_tools(d, _body([], question="hello"), 3)
    assert res == ([], [])
    assert res.dropped == 0


def test_reviewer_repro_b_consent_over_cap_drops_nothing():
    """THE SECOND REVIEWER REPRO, at the unit: 5 explicit picks under cap 3
    with a request that auto-arms nothing — all 5 armed, zero dropped. The
    cap yielded to consent; there was no candidate for it to cut."""
    d = _fake_d()
    five = _EXPLICIT + ["convert_document", "write_document"]
    res = _resolve_armed_tools(d, _body(five, question="hello"), 3)
    armed, auto = res
    assert armed == five
    assert auto == []
    assert res.dropped == 0


def test_cap_leaves_room_and_auto_fills_only_up_to_it():
    d = _fake_d()
    # One explicit pick + cap 2 -> exactly one auto slot; the web ask scores
    # web_search(8) then web_fetch(3), so only the winner fits and the rest
    # of the baseline's picks are the measured drop.
    res = _resolve_armed_tools(d, _body(_EXPLICIT[:1]), 2)
    armed, auto = res
    assert len(armed) == 2
    assert auto == ["web_search"]
    assert res.dropped >= 1
    assert res.ceiling == 2


def test_cap_never_widens_beyond_the_standing_max():
    """A hostile/buggy cap above _MAX_ARMED_TOOLS must clamp DOWN to it."""
    d = _fake_d()
    wide = _resolve_armed_tools(d, _body(_EXPLICIT[:1]), 99)
    assert wide == _resolve_armed_tools(d, _body(_EXPLICIT[:1]), None)
    assert len(wide[0]) <= _MAX_ARMED_TOOLS
    assert wide.dropped == 0  # a ceiling at the standing max cuts nothing


# --------------------------------------------------------------------------- #
# 2 — profile bands: what cap a weak model actually earns
# --------------------------------------------------------------------------- #


def test_max_tools_bands_drive_the_cap():
    assert _weak_profile(0.5).max_tools() == 3
    assert _weak_profile(0.8).max_tools() == 4
    assert _weak_profile(0.92).max_tools() == 6
    assert _weak_profile(0.99).max_tools() is None  # earned the full menu
    # Unmeasured (default floor) and trusted profiles never cap.
    assert CapabilityProfile(model_id="x").max_tools() is None


# --------------------------------------------------------------------------- #
# 3 — the POST lane: cap applied, adapted disclosed
# --------------------------------------------------------------------------- #


def test_weak_model_pick_arms_at_most_cap_and_payload_carries_adapted(
    client, monkeypatch
):
    """The POSITIVE case: the request WOULD have auto-armed web tools, the
    envelope ceiling genuinely cut them, so `adapted` discloses the ceiling
    that bit (a real drop, not merely an existing budget)."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))  # cap 3
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "provider": "ollama", "model": "tiny",
        "tools": _EXPLICIT, "auto_tools": True,
    })
    assert r.status_code == 200
    body = r.json()
    # Explicit picks all survived; the auto pass added NOTHING (cap 3 == the
    # explicit count, zero free slots) — web_search was DROPPED, which is
    # exactly what licenses the disclosure below.
    assert body["auto_armed"] == []
    names = _spec_names(seen["tools"])
    assert set(_EXPLICIT) <= names
    assert "web_search" not in names
    assert body["adapted"] == {"model": "tiny", "changes": ["tool_cap:3"]}


def test_reviewer_repro_a_plain_hello_on_a_weak_model_discloses_nothing(
    client, monkeypatch
):
    """WAVE-B REVIEWER REPRO (a), POST lane: a weak-pinned model + a plain
    "hello" armed nothing and dropped nothing — `adapted` must be null, or
    every turn of that model wears a permanent receipt line and the
    zero-noise guard is defeated."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny", "auto_tools": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["adapted"] is None
    assert body["auto_armed"] == []


def test_reviewer_repro_b_explicit_picks_over_cap_disclose_nothing(
    client, monkeypatch
):
    """WAVE-B REVIEWER REPRO (b), POST lane: 5 explicit picks under cap 3 —
    ALL FIVE arm (consent), and since nothing was dropped `adapted` is null.
    The old gate said "3 tools max" while 5 tools were armed: a false
    statement on the accountability surface."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    five = _EXPLICIT + ["convert_document", "write_document"]
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny",
        "tools": five, "auto_tools": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert set(five) <= _spec_names(seen["tools"])  # every pick armed
    assert body["adapted"] is None                  # nothing was dropped


def test_default_route_trusted_is_byte_identical_and_adapted_null(
    client, monkeypatch
):
    """MUTATION PIN: the same request WITHOUT the weak pin (config default =
    mock, trusted by construction) must arm exactly as it always did — the
    web tool auto-arms — and disclose nothing. A mutation that applies the
    cap unconditionally (or treats None as 0) goes red here; a mutation that
    never applies it goes red above."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "tools": _EXPLICIT, "auto_tools": True,
    })
    assert r.status_code == 200
    body = r.json()
    assert "web_search" in body["auto_armed"]
    assert "web_search" in _spec_names(seen["tools"])
    assert "adapted" in body  # null-always-present, like doors' []
    assert body["adapted"] is None


def test_cap_equal_to_the_standing_max_bends_nothing(client, monkeypatch):
    """native 0.92 -> max_tools() == 6 == _MAX_ARMED_TOOLS: the menu did not
    narrow, so disclosing "adapted" would be noise dressed as honesty."""
    assert _MAX_ARMED_TOOLS == 6  # the gate's premise — revisit if this moves
    _pin_weak_model(client, monkeypatch, _weak_profile(0.92))
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "provider": "ollama", "model": "tiny", "auto_tools": True,
    })
    body = r.json()
    assert body["adapted"] is None
    assert "web_search" in body["auto_armed"]  # arming untouched too


def test_a_broken_profile_lookup_never_breaks_the_turn(client, monkeypatch):
    def boom(provider, model):
        raise RuntimeError("envelope store on fire")

    monkeypatch.setattr(
        client.app.state.platform.providers, "capability_profile", boom
    )
    seen: dict = {}
    _capture_complete(client, monkeypatch, seen)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "auto_tools": True,
    })
    assert r.status_code == 200
    assert r.json()["adapted"] is None  # fail OPEN to today's behavior


# --------------------------------------------------------------------------- #
# 4 — lane parity: the stream done-frame carries the identical object
# --------------------------------------------------------------------------- #


def test_stream_lane_caps_and_discloses_identically(client, monkeypatch):
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "provider": "ollama", "model": "tiny",
        "tools": _EXPLICIT, "auto_tools": True,
    })
    assert done["auto_armed"] == []
    names = _spec_names(seen["tools"])
    assert set(_EXPLICIT) <= names
    assert "web_search" not in names
    assert done["adapted"] == {"model": "tiny", "changes": ["tool_cap:3"]}


def test_stream_default_route_carries_adapted_null_not_absent(client, monkeypatch):
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": _WEB_ASK}],
        "auto_tools": True,
    })
    assert "adapted" in done  # LANE PARITY on absent-vs-null: null, present
    assert done["adapted"] is None
    assert "web_search" in done["auto_armed"]


def test_stream_reviewer_repro_a_plain_hello_discloses_nothing(
    client, monkeypatch
):
    """Repro (a) on the STREAM lane — the lane the dashboard actually uses,
    where the permanent receipt line would have been rendered."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny", "auto_tools": True,
    })
    assert "adapted" in done
    assert done["adapted"] is None
    assert done["auto_armed"] == []


def test_stream_reviewer_repro_b_consent_over_cap_discloses_nothing(
    client, monkeypatch
):
    """Repro (b) on the STREAM lane: all five picks arm, adapted null."""
    _pin_weak_model(client, monkeypatch, _weak_profile(0.5))
    five = _EXPLICIT + ["convert_document", "write_document"]
    seen: dict = {}
    _capture_stream(client, monkeypatch, seen)
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny",
        "tools": five, "auto_tools": True,
    })
    assert set(five) <= _spec_names(seen["tools"])
    assert done["adapted"] is None


# --------------------------------------------------------------------------- #
# 5 — lock-step source pins (both lanes, byte-identical mechanism)
# --------------------------------------------------------------------------- #


def test_envelope_consult_and_payload_key_are_in_both_lanes():
    """The v1.144.0-class bug this repo documents is 'one lane got the fix':
    pin the cap threading AND the payload key in each lane's source."""
    for name in ("chat_turn.py", "routes/chat.py"):
        src = (_SRC / name).read_text(encoding="utf-8")
        assert "_resolve_armed_tools, d, body, _tool_cap" in src, (
            f"{name} stopped threading the envelope cap into arming"
        )
        assert '"adapted": envelope_adapted,' in src, (
            f"{name} lost the adapted payload key"
        )
        assert "capability_profile" in src, (
            f"{name} no longer consults the capability envelope"
        )
        # The disclosure gate is the MEASURED drop signal — a lane that goes
        # back to gating on the cap's existence re-ships both reviewer repros
        # (permanent receipt line on 'hello'; '3 tools max' beside 5 armed).
        assert "_selection.dropped > 0" in src, (
            f"{name}'s adapted gate no longer reads the measured drop signal"
        )
