"""v1.355.0 lane 5D fix round — a Gemini key keeps working past round one.

G46 made gemini-3.8-flash the default (the family Google gives NEW keys), and
the reviewer found three things between a pasted key and a working Gemini that
live in files this lane does not own. Each is pinned here through the REAL
code (the real adapter the real ProviderManager builds, the real agent
runtime, the real app factory); only the network answer is faked.

1. A Gemini 3 TOOL LOOP keeps its thought signatures. Gemini 3 answers 400
   when a ``functionCall`` of the current turn comes back without the
   ``thoughtSignature`` it was sent with (Vertex AI "Thought signatures", read
   2026-10-19: "If a required thought signature is not returned when using
   Gemini 3 models, the model will return a 400 error"; the Gemini API
   thinking guide: "You MUST always resend all thought blocks exactly as they
   were received from the model"). ``adapters/google.py`` rebuilt the model
   turn as ``{functionCall: {name, args}}`` and dropped the signature, so
   every tool-armed Gemini turn failed at its SECOND request. Now the parts
   ride ``LLMResponse.raw_blocks`` as one carrier (the claude-cli shape:
   replayed verbatim only while the turn still says what the model wrote), the
   agent runtime copies ``raw_blocks`` onto the assistant turn like both chat
   lanes already do, a rebuilt turn on Gemini 3+ carries Google's documented
   ``skip_thought_signature_validator`` on its first call, and the Anthropic
   adapter never replays Gemini's carrier.
2. The model MENU offers Geminis that exist: ``agents/dynamic.KNOWN_MODELS``
   listed gemini-2.0-flash (shut down) and gemini-1.5-pro (retired), and the
   ModelSwitcher dial's tiers named 1.5/2.0 ids.
3. The card's method chip says how the credential was made: the registry's
   rows carry ``connected_via`` (the stored record's own method).

Every pin that needs an edit in a file this lane does not own is a STRICT
xfail naming the edit; the edits are one patch the integrator applies
(``5d_integrator.patch`` from the lane's notes), after which each marker is
taken off on purpose and the pin must pass.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters import google as gmod
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall
from iron_jarvis.providers.manager import GEMINI_DEFAULT_MODEL, ProviderManager

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"

#: A key in Google's documented "auth key" shape. Never a real key.
KEY = "AQ.Ab8RN6L-lane5d-tool-loop-key-0123456789"
SIG = "CiQBcsjafE-lane5d-signature-A"
SIG_B = "CiQBcsjafE-lane5d-signature-B"
SKIP = "skip_thought_signature_validator"

_PATCH = "the lane-5D integrator patch (5d_integrator.patch)"


def _needs(edit: str):
    return pytest.mark.xfail(
        strict=True,
        reason=f"INTEGRATOR: {edit} -- in {_PATCH}. Remove this marker when it lands.",
    )


def _read(path: Path) -> str:
    # CRLF on the CI runner, LF here: normalise at the READER.
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


# --------------------------------------------------------------------------- #
# A fake Generative Language API (both request shapes)
# --------------------------------------------------------------------------- #
def _fc_part(name: str, args: dict, sig: str | None) -> dict:
    part: dict = {"functionCall": {"name": name, "args": args}}
    if sig:
        part["thoughtSignature"] = sig
    return part


def _answer(parts: list[dict], finish: str = "STOP") -> dict:
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2},
    }


class _Gemini:
    """Answers each request in turn, recording the JSON body it was sent.
    ``generateContent`` answers JSON; ``streamGenerateContent`` answers SSE
    (one chunk per part, so a signature arrives on its own part)."""

    def __init__(self, answers: list[dict]) -> None:
        self.answers = list(answers)
        self.bodies: list[dict] = []
        self.urls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        self.bodies.append(json.loads(request.content or b"{}"))
        # Anything after the script (a post-run call) gets a plain answer.
        data = self.answers.pop(0) if self.answers else _answer([{"text": "ok"}])
        if "streamGenerateContent" in str(request.url):
            chunks = []
            for part in data["candidates"][0]["content"]["parts"]:
                chunks.append({"candidates": [{"content": {"role": "model", "parts": [part]}}]})
            chunks.append(
                {
                    "candidates": [{"content": {"role": "model", "parts": []},
                                    "finishReason": data["candidates"][0]["finishReason"]}],
                    "usageMetadata": data["usageMetadata"],
                }
            )
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=sse)
        return httpx.Response(200, json=data)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _adapter(fake: _Gemini, model: str = GEMINI_DEFAULT_MODEL):
    """The adapter the REAL ProviderManager builds for a pasted key."""
    pm = ProviderManager(credential_resolver=lambda name: KEY if name == "google" else None)
    adapter = pm.get("google", model)
    adapter._http = fake.client()
    return adapter


def _model_turns(body: dict) -> list[dict]:
    return [c for c in body["contents"] if c.get("role") == "model"]


async def _drain_final(agen):
    final = None
    async for frame in agen:
        if frame.get("type") == "final":
            final = frame["response"]
    return final


def _round_two(resp, task: str = "read a.txt") -> list[LLMMessage]:
    """The messages a lane sends for round two: the user's ask, the model's
    turn WITH its raw_blocks (chat_turn / routes/chat / the runtime), and each
    call's result."""
    return [
        LLMMessage(role="user", content=task),
        LLMMessage(
            role="assistant",
            content=resp.text,
            tool_calls=resp.tool_calls,
            raw_blocks=list(resp.raw_blocks or []),
        ),
        *[
            LLMMessage(role="tool", tool_call_id=tc.id, name=tc.name, content="hello")
            for tc in resp.tool_calls
        ],
    ]


# --------------------------------------------------------------------------- #
# 1. The tool loop keeps its signatures
# --------------------------------------------------------------------------- #
@_needs("providers/adapters/google.py keeps each part's thoughtSignature (GEMINI_CARRIER) and replays it")
@pytest.mark.parametrize("path", ["complete", "stream"])
def test_round_two_of_a_gemini_3_tool_loop_carries_the_signature(path):
    """The reviewer's case: round one asks for a tool with a signature; round
    two must send that part back exactly. MUTATION: rebuild the turn from
    text + calls (the pre-fix shape) -> no signature -> red."""
    first = [_fc_part("read_file", {"path": "a.txt"}, SIG)]
    fake = _Gemini([_answer(first), _answer([{"text": "It says hello."}])])
    adapter = _adapter(fake)

    async def go():
        if path == "complete":
            r1 = await adapter.complete(system="", messages=[LLMMessage(role="user", content="read a.txt")],
                                        tools=[{"name": "read_file", "input_schema": {"type": "object"}}])
            r2 = await adapter.complete(system="", messages=_round_two(r1),
                                        tools=[{"name": "read_file", "input_schema": {"type": "object"}}])
        else:
            r1 = await _drain_final(adapter.stream(
                system="", messages=[LLMMessage(role="user", content="read a.txt")],
                tools=[{"name": "read_file", "input_schema": {"type": "object"}}]))
            r2 = await _drain_final(adapter.stream(
                system="", messages=_round_two(r1),
                tools=[{"name": "read_file", "input_schema": {"type": "object"}}]))
        return r1, r2

    r1, r2 = asyncio.run(go())
    assert [tc.name for tc in r1.tool_calls] == ["read_file"]
    assert r2.text == "It says hello."
    if path == "stream":
        assert all("streamGenerateContent" in u for u in fake.urls)
    sent = _model_turns(fake.bodies[1])
    assert sent == [{"role": "model", "parts": first}]


@_needs("providers/adapters/google.py replays a parallel turn's parts verbatim, in order")
def test_parallel_calls_go_back_in_the_order_received_with_the_one_signature():
    """Parallel calls: "only the first functionCall part will contain the
    thought_signature" and the parts must come back in the order received."""
    first = [
        {"text": "Reading both."},
        _fc_part("read_file", {"path": "a.txt"}, SIG),
        _fc_part("read_file", {"path": "b.txt"}, None),
    ]
    fake = _Gemini([_answer(first), _answer([{"text": "done"}])])
    adapter = _adapter(fake)

    async def go():
        r1 = await adapter.complete(system="", messages=[LLMMessage(role="user", content="read both")], tools=[])
        await adapter.complete(system="", messages=_round_two(r1, "read both"), tools=[])
        return r1

    r1 = asyncio.run(go())
    assert len(r1.tool_calls) == 2
    assert _model_turns(fake.bodies[1]) == [{"role": "model", "parts": first}]


@_needs("providers/adapters/google.py puts skip_thought_signature_validator on a rebuilt Gemini 3 turn")
def test_a_turn_gemini_did_not_write_gets_googles_skip_value_on_its_first_call():
    """Another provider's turn after a failover, a resumed run: no carrier.
    Gemini 3 needs a signature on the first call of the step; Google documents
    one value for calls the API did not write. Only the FIRST call carries it
    (the parallel rule)."""
    msgs = [
        LLMMessage(role="user", content="read both"),
        LLMMessage(role="assistant", content="", tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
            ToolCall(id="c2", name="read_file", arguments={"path": "b.txt"}),
        ]),
    ]
    model_turn = _model_turns({"contents": gmod.GoogleAdapter._to_contents(msgs, "gemini-3.8-flash")})[0]
    assert model_turn["parts"][0]["thoughtSignature"] == SKIP
    assert "thoughtSignature" not in model_turn["parts"][1]
    assert model_turn["parts"][0]["functionCall"] == {"name": "read_file", "args": {"path": "a.txt"}}


def test_control_an_older_gemini_turn_is_rebuilt_exactly_as_before():
    """A model that does not validate signatures (2.5) keeps the pre-fix
    request byte for byte, through the real adapter's request; so does the
    static builder called with no model (its old call shape)."""
    msgs = [
        LLMMessage(role="user", content="read"),
        LLMMessage(role="assistant", content="ok", tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"path": "a.txt"}),
        ]),
        LLMMessage(role="tool", tool_call_id="c1", name="read_file", content="hello"),
    ]
    want = {"role": "model", "parts": [
        {"text": "ok"},
        {"functionCall": {"name": "read_file", "args": {"path": "a.txt"}}},
    ]}
    fake = _Gemini([_answer([{"text": "done"}])])
    adapter = _adapter(fake, "gemini-2.5-flash")
    asyncio.run(adapter.complete(system="", messages=msgs, tools=[]))
    assert _model_turns(fake.bodies[0]) == [want]
    assert _model_turns({"contents": gmod.GoogleAdapter._to_contents(msgs)}) == [want]


@_needs("providers/adapters/google.py never attaches a signature to a turn that was edited")
def test_an_edited_turn_never_carries_a_signature_it_was_not_written_with():
    """The claude-cli rule: a turn whose calls changed since the model wrote
    them is rebuilt -- with Google's skip value, never the stale signature."""
    first = [_fc_part("read_file", {"path": "a.txt"}, SIG)]
    fake = _Gemini([_answer(first)])
    adapter = _adapter(fake)
    r1 = asyncio.run(adapter.complete(system="", messages=[LLMMessage(role="user", content="x")], tools=[]))
    assert r1.raw_blocks, "the response carries its parts"
    edited = _round_two(r1)
    edited[1].tool_calls = [ToolCall(id="read_file", name="read_file", arguments={"path": "OTHER.txt"})]
    turn = _model_turns({"contents": gmod.GoogleAdapter._to_contents(edited, GEMINI_DEFAULT_MODEL)})[0]
    assert turn["parts"][0]["functionCall"]["args"] == {"path": "OTHER.txt"}
    assert turn["parts"][0]["thoughtSignature"] == SKIP
    assert SIG not in json.dumps(turn)


@_needs("providers/adapters/google.py writes no carrier when nothing was signed")
def test_a_response_with_no_signature_or_no_call_carries_nothing():
    """Nothing to keep -> ``raw_blocks == []`` (every older fixture and every
    plain answer is unchanged)."""
    fake = _Gemini([
        _answer([_fc_part("read_file", {"path": "a.txt"}, None)]),
        _answer([{"text": "plain", "thoughtSignature": SIG}]),
        _answer([_fc_part("read_file", {"path": "a.txt"}, SIG)]),
    ])
    adapter = _adapter(fake)

    async def go():
        u = [LLMMessage(role="user", content="x")]
        return [await adapter.complete(system="", messages=u, tools=[]) for _ in range(3)]

    unsigned, plain, signed = asyncio.run(go())
    assert unsigned.raw_blocks == [] and plain.raw_blocks == []
    assert len(signed.raw_blocks) == 1 and signed.raw_blocks[0]["type"] == "gemini_native"


@_needs("providers/adapters/anthropic.py _own_blocks leaves out the gemini_native carrier")
def test_the_anthropic_adapter_never_replays_geminis_parts():
    """A turn that moves from Gemini to Claude (an Auto turn's failover) must
    not hand Gemini parts to the Messages API as content blocks."""
    from iron_jarvis.providers.adapters import anthropic

    carrier = {"type": "gemini_native", "version": 1, "parts": [_fc_part("x", {}, SIG)], "projection": "{}"}
    thinking = {"type": "thinking", "thinking": "t", "signature": "s"}
    msg = LLMMessage(role="assistant", content="", tool_calls=[ToolCall(id="x", name="x")],
                     raw_blocks=[carrier, thinking])
    assert anthropic._own_blocks(msg) == [thinking]


# --------------------------------------------------------------------------- #
# The loop through the REAL agent runtime
# --------------------------------------------------------------------------- #
@_needs("agents/runtime.py copies resp.raw_blocks onto the assistant turn (+ google.py's carrier)")
def test_an_agent_runs_gemini_tool_loop_sends_the_signature_back(tmp_path):
    """The real perceive->act loop with the real Gemini adapter the manager
    builds for a key: step one asks for read_file with a signature, step two
    must carry it. Before the fix the runtime dropped ``raw_blocks`` (only the
    chat lanes copied them). MUTATION: drop the runtime's ``raw_blocks=`` ->
    the step-two turn is rebuilt -> red."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentDefinition
    from iron_jarvis.core.models import AgentType, SessionStatus
    from iron_jarvis.platform import build_platform

    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    first = [_fc_part("read_file", {"path": "a.txt"}, SIG)]
    fake = _Gemini([_answer(first), _answer([{"text": "It says hello."}])])
    adapter = _adapter(fake)
    p.providers.register("mock", lambda model=None: adapter)

    async def go():
        s = await orch.create_session("read a.txt and tell me what it says", AgentType.BUILDER)
        Path(s.workspace_path, "a.txt").write_text("hello from a", encoding="utf-8")
        defn = AgentDefinition(type=AgentType.BUILDER, system_prompt="t", tools=["read_file"])
        return await asyncio.wait_for(orch.run_session(s.id, defn), timeout=60)

    row = asyncio.run(go())
    assert row.status is SessionStatus.COMPLETED, row.summary
    assert len(fake.bodies) >= 2
    assert not _model_turns(fake.bodies[0])  # step one: the task only
    assert _model_turns(fake.bodies[1]) == [{"role": "model", "parts": first}]


# --------------------------------------------------------------------------- #
# 2. The menus offer Geminis that exist
# --------------------------------------------------------------------------- #
_CURRENT = {"gemini-3.8-flash", "gemini-3.5-flash-lite"}


@_needs("agents/dynamic.py KNOWN_MODELS google rows = gemini-3.8-flash, gemini-3.5-flash-lite")
def test_the_model_menu_lists_only_current_geminis():
    """gemini-2.0-flash was shut down and gemini-1.5-pro retired; the two
    Google names "for any new projects" are both Stable (docs read 2026-10-19).
    The default a pasted key gets is one of them."""
    from iron_jarvis.agents.dynamic import available_models

    google = {m["model"] for m in available_models() if m["provider"] == "google"}
    assert google == _CURRENT
    assert GEMINI_DEFAULT_MODEL in google


@_needs("dashboard/components/ModelSwitcher.tsx TIERS.google = 3.5-flash-lite / 3.8-flash / 3.8-flash")
def test_the_quality_dial_names_current_geminis():
    src = _read(DASH / "components" / "ModelSwitcher.tsx")
    block = re.search(r"\n  google: \{\n(.*?)\n  \},", src, re.S)
    assert block, "the dial's google tiers"
    tiers = dict(re.findall(r'(\w+): "([^"]+)"', block.group(1)))
    assert tiers == {
        "fast": "gemini-3.5-flash-lite",
        "balanced": GEMINI_DEFAULT_MODEL,
        "best": GEMINI_DEFAULT_MODEL,
    }
    assert not re.search(r'"gemini-(1|2)\.', src)


# --------------------------------------------------------------------------- #
# 3. The card says how the credential was made
# --------------------------------------------------------------------------- #
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setenv("IRONJARVIS_IRON_PROXY_BUNDLE", str(tmp_path / "no-bundle.mjs"))
    monkeypatch.delenv("IRONJARVIS_IRON_PROXY_NODE", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    async def _no_watch(*_a, **_k):
        await asyncio.Event().wait()

    monkeypatch.setattr("iron_jarvis.iron_proxy.service.watch", _no_watch)
    proj = tmp_path / "proj"
    proj.mkdir()
    with TestClient(create_app(str(proj))) as c:
        yield c


def _row(c: TestClient, provider: str) -> dict:
    rows = {r["provider"]: r for r in c.get("/connections").json()["connections"]}
    return rows[provider]


@_needs("connections/registry.py status() rows carry connected_via (the record's own method)")
def test_the_card_says_whether_google_was_connected_by_key_or_by_login(client):
    """``method`` is the spec's primary way ("api_key" for Google now), so a
    login-connected card read "API key". MUTATION: report ``spec.method`` as
    connected_via -> the login reads api_key -> red."""
    assert _row(client, "google")["connected_via"] == ""  # not connected
    assert client.post("/connections/google/key", json={"key": KEY}).status_code == 200
    row = _row(client, "google")
    assert row["method"] == "api_key" and row["connected_via"] == "api_key"
    assert KEY not in json.dumps(row)

    p = client.app.state.d.platform
    assert client.delete("/connections/google").status_code == 200
    p.secrets.set_oauth("google_oauth", {"access_token": "ya29.lane5d-login"})
    p.connections._upsert("google", method="oauth", status="connected", secret_name="google_oauth")
    row = _row(client, "google")
    assert row["method"] == "api_key"  # the spec's primary way, unchanged
    assert row["connected_via"] == "oauth"
