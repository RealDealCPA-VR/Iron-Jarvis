"""The token estimator uses the MEASURED chars-per-token ratio (v1.203.0, C5).

Contract under test (docs/IRONCORE-INTEGRATION.md Wave C, item C5):

* ``estimate_tokens(text)`` — no ratio — is BYTE-IDENTICAL to pre-v1.203.0:
  ``None`` resolves to the module constant ``CHARS_PER_TOKEN`` exactly, so
  every unmeasured route estimates the same number it always did (pinned
  literals for a mixed CJK/latin string, mutation-sensitive);
* a measured ratio replaces ONLY the non-CJK divisor — the TOKEN-RATIO probe
  measures latin-ish filler, so the CJK constant keeps covering the 4x CJK
  density error exactly as before;
* the accepted ratio is clamped to [1.0, 8.0] defensively: the probe clamps
  too, but the value crosses a JSON store between the probe and this divisor,
  and a corrupt 0.0 must not zero a budget (nor a 1e9 fake-fit an overflow);
* ``plan_history`` / ``plan_agent_transcript`` thread the ratio into EVERY
  internal estimate (and the token->char back-conversion of the clip paths) —
  ratio ``None`` produces plans identical to the no-kwarg call, and a
  measured ratio honestly changes the fit (a transcript that fits at the
  default overflows at 1.5 -> more dropped, reported, task/tool-pair rules
  intact);
* LANE PLUMBING: both chat lanes ride the shared ``_plan_context``, which
  passes ``profile.chars_per_token`` to ``plan_history`` ONLY when
  ``profile.field_measured("chars_per_token")`` — the per-field provenance
  rule: an unmeasured value must not masquerade as measured even when the
  number would coincide (same outcome, wrong principle);
* the agent lane's ``plan_agent_transcript`` gains the parameter with default
  ``None`` so ``agents/runtime.py`` (owned elsewhere this wave) is
  byte-identical until the coordinator lands its one-line pass.

Fully offline; the chat harness is the same router-monkeypatch shape as
``tests/test_chat_envelope_v1202.py``.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.context.agent_window import plan_agent_transcript
from iron_jarvis.context.budget import (
    CHARS_PER_TOKEN,
    MEASURED_RATIO_MAX,
    MEASURED_RATIO_MIN,
    effective_chars_per_token,
    estimate_tokens,
    plan_history,
)
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _history_ratio
from iron_jarvis.envelope.profile import CapabilityProfile
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"

#: 420 latin chars + 72 CJK chars — both branches of the estimator at once.
_MIXED = ("The quick brown fox. " * 20) + ("日本語テスト" * 12)
_LATIN = "a" * 400
_CJK = "日" * 100


# --------------------------------------------------------------------------- #
# 1 — estimate_tokens: byte-identical default, latin-only ratio, clamps
# --------------------------------------------------------------------------- #


def test_none_is_byte_identical_pinned_literals():
    """MUTATION PIN: the exact pre-v1.203.0 numbers. int(other/3.6 +
    cjk/1.1) + 1 — a divisor drift of any kind goes red on the literals,
    and the no-kwarg call and the explicit-None call are the SAME estimate."""
    assert len(_MIXED) == 492
    assert estimate_tokens(_MIXED) == 183
    assert estimate_tokens(_MIXED, None) == 183
    assert estimate_tokens(_LATIN) == 112
    assert estimate_tokens(_CJK) == 91
    assert estimate_tokens("") == 0 and estimate_tokens("", 2.0) == 0
    # None resolves to the module constant EXACTLY — not a lookalike copy.
    assert effective_chars_per_token(None) is CHARS_PER_TOKEN or (
        effective_chars_per_token(None) == CHARS_PER_TOKEN
    )


def test_measured_ratio_replaces_only_the_latin_divisor():
    """A measured 2.0 vs the unmeasured-default 4.0: the latin token count
    doubles (halved chars-per-token), pinned exactly. Pure CJK text is
    UNTOUCHED by any ratio — the probe measured latin filler and has no
    evidence about CJK density."""
    assert estimate_tokens(_LATIN, 4.0) == 101  # int(400/4.0) + 1
    assert estimate_tokens(_LATIN, 2.0) == 201  # int(400/2.0) + 1
    assert estimate_tokens(_CJK, 2.0) == estimate_tokens(_CJK) == 91
    assert estimate_tokens(_CJK, 1.0) == 91
    # Mixed: only the latin share moves — pinned.
    assert estimate_tokens(_MIXED, 2.0) == 276


def test_clamp_pins():
    """[1.0, 8.0] defensively: a corrupt store 0.0 must not zero a budget
    (0-divisor -> infinite cost -> everything dropped), and 1e9 must not
    make everything 'fit'. Non-finite junk falls back to the default."""
    assert effective_chars_per_token(0.0) == MEASURED_RATIO_MIN == 1.0
    assert effective_chars_per_token(-3.0) == 1.0
    assert effective_chars_per_token(1e9) == MEASURED_RATIO_MAX == 8.0
    assert estimate_tokens(_LATIN, 0.5) == estimate_tokens(_LATIN, 1.0) == 401
    assert estimate_tokens(_LATIN, 100.0) == estimate_tokens(_LATIN, 8.0) == 51
    for junk in (float("nan"), float("inf"), float("-inf"), "not a number"):
        assert effective_chars_per_token(junk) == CHARS_PER_TOKEN
        assert estimate_tokens(_MIXED, junk) == 183


# --------------------------------------------------------------------------- #
# 2 — plan_history: None == today; a measured ratio changes the fit honestly
# --------------------------------------------------------------------------- #


def _chat_messages(n: int = 10, chars: int = 400):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * chars}
        for i in range(n)
    ]


def test_plan_history_none_matches_the_no_kwarg_call_exactly():
    """REGRESSION PIN: dataclass equality across the WHOLE plan (messages,
    counters, recap, used/raw tokens) between today's call shape and an
    explicit ``chars_per_token=None`` — plus a pinned used_tokens so the
    arithmetic itself is anchored, not just internal consistency."""
    msgs = _chat_messages()
    today = plan_history(msgs, window=2000, system_text="sys")
    explicit = plan_history(msgs, window=2000, system_text="sys", chars_per_token=None)
    assert today == explicit
    assert today.dropped == 0 and not today.recap
    # sys = 3 chars -> 1 token; 10 * (int(400/3.6)+1+4) = 10 * 116 = 1160.
    assert today.used_tokens == 1161


def test_plan_history_measured_ratio_changes_the_fit_honestly():
    """A conversation that FITS at the default (3.6) OVERFLOWS at a measured
    1.5 — more dropped, honestly reported (recap + suggest_larger), and the
    newest message survives per the chat planner's contract."""
    msgs = _chat_messages()  # 10 x 400 chars, window 2000 -> budget 1488
    fits = plan_history(msgs, window=2000, chars_per_token=None)
    assert fits.dropped == 0 and len(fits.messages) == 10

    tight = plan_history(msgs, window=2000, chars_per_token=1.5)
    # cost/message = int(400/1.5)+1+4 = 271; budget 1488-300 recap = 1188
    # -> newest 4 fit (1084), 6 dropped.
    assert tight.dropped == 6
    assert len(tight.messages) == 4
    assert tight.messages[-1]["content"] == msgs[-1]["content"]  # newest kept
    assert tight.recap  # the drop is disclosed, never silent
    assert tight.suggest_larger
    # raw demand is counted at the SAME ratio as the fit — the two counters
    # must not disagree (the half-threaded-ratio failure mode).
    assert tight.raw_tokens > fits.raw_tokens


def test_plan_history_clip_path_converts_chars_at_the_measured_ratio():
    """The token->char back-conversion of the clip uses the SAME ratio as the
    estimates: at a measured 1.5 the single overlong message is clipped to
    ~budget*1.5 chars, not budget*3.6 — a 3.6-char clip would still overflow
    the budget it was cut to fit."""
    huge = [{"role": "user", "content": "y" * 20_000}]
    at_default = plan_history(huge, window=1200, chars_per_token=None)
    at_measured = plan_history(huge, window=1200, chars_per_token=1.5)
    assert at_default.clipped_last and at_measured.clipped_last
    assert len(at_measured.messages[0]["content"]) < len(
        at_default.messages[0]["content"]
    )
    # And the measured clip actually fits its own budget again.
    assert (
        estimate_tokens(at_measured.messages[0]["content"], 1.5)
        <= at_measured.history_budget
    )


def test_a_corrupt_zero_ratio_cannot_zero_a_history_budget():
    """The defensive clamp at the planner level: 0.0 behaves as 1.0 — big
    and conservative, but finite — never as divide-by-~zero."""
    msgs = _chat_messages(4, 100)
    assert plan_history(msgs, window=8000, chars_per_token=0.0) == plan_history(
        msgs, window=8000, chars_per_token=1.0
    )
    kept = plan_history(msgs, window=8000, chars_per_token=0.0)
    assert kept.dropped == 0 and len(kept.messages) == 4


# --------------------------------------------------------------------------- #
# 3 — plan_agent_transcript: same contract, agent-side invariants intact
# --------------------------------------------------------------------------- #


def _agent_messages():
    task = SimpleNamespace(role="user", content="T" * 200, tool_calls=None)
    steps = [
        SimpleNamespace(role="assistant", content=f"step {i} " + "z" * 392,
                        tool_calls=None)
        for i in range(8)
    ]
    return [task, *steps]


def test_plan_agent_transcript_none_matches_the_no_kwarg_call_exactly():
    msgs = _agent_messages()
    today = plan_agent_transcript(msgs, window=2000, system_text="")
    explicit = plan_agent_transcript(
        msgs, window=2000, system_text="", chars_per_token=None
    )
    assert today == explicit
    assert today.dropped_blocks == 0 and not today.changed
    assert today.messages == msgs  # untouched when it fits — the fast path


def test_plan_agent_transcript_measured_ratio_changes_the_fit_honestly():
    """Fits at the default, overflows at 1.5: blocks drop OLDEST-first and the
    TASK (messages[0]) survives verbatim — the v1.152.0 rule a threaded ratio
    must not bend."""
    msgs = _agent_messages()
    fits = plan_agent_transcript(msgs, window=2000, chars_per_token=None)
    assert fits.dropped_blocks == 0

    tight = plan_agent_transcript(msgs, window=2000, chars_per_token=1.5)
    # task 138 + 8 x 271 = 2306 > 1488; budget-recap 1188 -> task + newest 3.
    assert tight.dropped_blocks == 5
    assert len(tight.messages) == 4
    assert tight.messages[0].content == msgs[0].content  # the task, unclipped
    assert not tight.clipped_task
    assert tight.messages[-1].content == msgs[-1].content  # newest step kept
    assert tight.recap  # dropped work disclosed to the SYSTEM prompt
    assert tight.raw_tokens > fits.raw_tokens


def test_plan_agent_transcript_never_splits_a_tool_pair_under_a_ratio():
    """The indivisible-block rule under a measured ratio: a surviving
    assistant turn with tool_calls keeps its role='tool' results, and a
    dropped one loses them together."""
    task = SimpleNamespace(role="user", content="T" * 100, tool_calls=None)
    blocks = []
    for i in range(6):
        blocks.append(
            SimpleNamespace(role="assistant", content=f"call {i}",
                            tool_calls=[SimpleNamespace(name=f"tool_{i}")])
        )
        blocks.append(SimpleNamespace(role="tool", content="r" * 400,
                                      tool_calls=None))
    msgs = [task, *blocks]
    tight = plan_agent_transcript(msgs, window=1600, chars_per_token=1.5)
    assert tight.dropped_blocks > 0
    kept = tight.messages
    for i, m in enumerate(kept):
        if getattr(m, "role", "") == "tool":
            assert getattr(kept[i - 1], "role", "") == "assistant" and getattr(
                kept[i - 1], "tool_calls", None
            ), "a tool result survived without the turn that requested it"
    for i, m in enumerate(kept):
        if getattr(m, "tool_calls", None):
            assert i + 1 < len(kept) and getattr(kept[i + 1], "role", "") == "tool", (
                "a tool_use survived without its tool_result"
            )


def test_agent_lane_signature_default_keeps_runtime_byte_identical():
    """agents/runtime.py is owned by another agent this wave: the parameter
    default MUST be None so its unedited call site behaves exactly as before
    (the coordinator lands the one-line pass separately)."""
    assert (
        inspect.signature(plan_agent_transcript).parameters["chars_per_token"].default
        is None
    )
    assert (
        inspect.signature(plan_history).parameters["chars_per_token"].default is None
    )
    runtime = (_SRC / "agents" / "runtime.py").read_text(encoding="utf-8")
    assert "plan_agent_transcript(" in runtime  # the call site this protects


# --------------------------------------------------------------------------- #
# 4 — _history_ratio: the provenance gate, resolution, and failure honesty
# --------------------------------------------------------------------------- #


def _measured_profile(ratio: float = 2.0) -> CapabilityProfile:
    return CapabilityProfile(
        model_id="tiny",
        provider="ollama",
        source="probed",
        probed_at="2026-08-22T00:00:00+00:00",
        chars_per_token=ratio,
        measured_fields=["chars_per_token"],
    )


def _unmeasured_ratio_profile() -> CapabilityProfile:
    """A probed profile whose chars_per_token is NOT battery evidence — the
    value is deliberately NOT the 4.0 default, so a mutation that passes
    ``prof.chars_per_token`` without the field_measured gate goes red."""
    return CapabilityProfile(
        model_id="tiny",
        provider="ollama",
        source="probed",
        probed_at="2026-08-22T00:00:00+00:00",
        chars_per_token=2.5,
        measured_fields=["tool_protocols.native"],
    )


def _fake_d(profiler, default_provider="ollama", default_model="tiny"):
    return SimpleNamespace(
        platform=SimpleNamespace(
            providers=SimpleNamespace(capability_profile=profiler),
            config=SimpleNamespace(
                default_provider=default_provider, default_model=default_model
            ),
        )
    )


def test_history_ratio_measured_and_provenance_gated():
    d = _fake_d(lambda p, m: _measured_profile(2.0))
    assert _history_ratio(d, "ollama", "tiny") == 2.0
    d2 = _fake_d(lambda p, m: _unmeasured_ratio_profile())
    assert _history_ratio(d2, "ollama", "tiny") is None  # wrong pedigree -> None


def test_history_ratio_resolves_empty_pick_to_the_config_defaults():
    """Mirrors _context_window's resolution: the composer usually sends NO
    provider, and the ratio must describe the model that will actually
    answer — the config default — not fail on the empty string."""
    seen: list[tuple[str, str]] = []

    def profiler(p, m):
        seen.append((p, m))
        return _measured_profile(3.0)

    d = _fake_d(profiler, default_provider="fleet", default_model="qwen")
    assert _history_ratio(d, "", "") == 3.0
    assert seen == [("fleet", "qwen")]


def test_history_ratio_never_raises():
    def boom(p, m):
        raise RuntimeError("envelope store on fire")

    assert _history_ratio(_fake_d(boom), "ollama", "tiny") is None
    # No providers manager at all (minimal test platforms).
    assert _history_ratio(SimpleNamespace(platform=SimpleNamespace()), "a", "b") is None


# --------------------------------------------------------------------------- #
# 5 — lane plumbing: a measured ratio actually reaches plan_history
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _pin_profile(client, monkeypatch, profile: CapabilityProfile):
    providers = client.app.state.platform.providers
    real = providers.capability_profile

    def fake(provider: str, model: str) -> CapabilityProfile:
        if (provider, model) == ("ollama", "tiny"):
            return profile
        return real(provider, model)

    monkeypatch.setattr(providers, "capability_profile", fake)


def _spy_plan_history(monkeypatch, seen: list):
    """Record the chars_per_token kwarg _plan_context hands the planner.
    ``_plan_context`` imports ``plan_history`` from the package at call time,
    so patching the package attribute intercepts BOTH lanes."""
    import iron_jarvis.context as ctx

    real = ctx.plan_history

    def spy(messages, **kw):
        assert "chars_per_token" in kw, "_plan_context stopped passing the ratio"
        seen.append(kw["chars_per_token"])
        return real(messages, **kw)

    monkeypatch.setattr(ctx, "plan_history", spy)


def _capture_complete(client, monkeypatch):
    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    monkeypatch.setattr(client.app.state.platform.router, "complete", fake_complete)


def _capture_stream(client, monkeypatch):
    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"),
               "provider": "mock", "model": "mock"}

    monkeypatch.setattr(client.app.state.platform.router, "stream", fake_stream)


def test_post_lane_measured_ratio_reaches_plan_history(client, monkeypatch):
    _pin_profile(client, monkeypatch, _measured_profile(2.0))
    seen: list = []
    _spy_plan_history(monkeypatch, seen)
    _capture_complete(client, monkeypatch)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny",
    })
    assert r.status_code == 200
    assert seen == [2.0]


def test_post_lane_unmeasured_profile_passes_none(client, monkeypatch):
    """SAME OUTCOME, WRONG PRINCIPLE pin: the profile CARRIES 2.5 but the
    field is not battery evidence — None must reach the planner, keeping the
    estimate on the pinned default."""
    _pin_profile(client, monkeypatch, _unmeasured_ratio_profile())
    seen: list = []
    _spy_plan_history(monkeypatch, seen)
    _capture_complete(client, monkeypatch)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny",
    })
    assert r.status_code == 200
    assert seen == [None]


def test_post_lane_default_route_passes_none(client, monkeypatch):
    """The common case — no explicit pick, mock default (trusted, never
    measured): today's plan, byte-identical."""
    seen: list = []
    _spy_plan_history(monkeypatch, seen)
    _capture_complete(client, monkeypatch)
    r = client.post("/chat", json={
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert r.status_code == 200
    assert seen == [None]


def test_stream_lane_measured_ratio_reaches_plan_history(client, monkeypatch):
    """LANE PARITY: the streaming lane — the one the dashboard actually
    uses — rides the SAME shared _plan_context, so the same ratio arrives."""
    _pin_profile(client, monkeypatch, _measured_profile(2.0))
    seen: list = []
    _spy_plan_history(monkeypatch, seen)
    _capture_stream(client, monkeypatch)
    with client.stream("POST", "/chat/stream", json={
        "messages": [{"role": "user", "content": "hello"}],
        "provider": "ollama", "model": "tiny",
    }) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data: "):
                json.loads(line[6:])
    assert seen == [2.0]


# --------------------------------------------------------------------------- #
# 6 — lock-step source pins
# --------------------------------------------------------------------------- #


def test_both_lanes_ride_the_one_shared_planner_call():
    """The ratio lives in ONE place — chat_turn._plan_context — and the
    stream lane reaches it by importing that function, never by a second
    plan_history call that could silently miss the kwarg (the v1.144.0-class
    'one lane got the fix' failure)."""
    chat_turn = (_SRC / "daemon" / "chat_turn.py").read_text(encoding="utf-8")
    assert "chars_per_token=_history_ratio(d, provider, model)" in chat_turn
    assert 'field_measured("chars_per_token")' in chat_turn
    routes = (_SRC / "daemon" / "routes" / "chat.py").read_text(encoding="utf-8")
    assert "_plan_context," in routes or "_plan_context(" in routes
    assert "plan_history(" not in routes, (
        "routes/chat.py grew its own plan_history call — thread the ratio "
        "there too or route it back through _plan_context"
    )
