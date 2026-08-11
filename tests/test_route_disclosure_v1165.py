"""Chat replies are ACCOUNTABLE: route disclosure + full file disclosure (v1.165.0).

THE REPORT THIS EXTENDS. A user's default provider (a fleet endpoint) was
unreachable and the offline mock answered "Done. Wrote RESULT.md" with ZERO UI
signal. v1.162.0 made the router refuse that turn; this wave makes every turn
carry the route story SERVER-SIDE, because the dashboard's "answered by X" chip
computes client-side against the EXPLICIT pick only — and chat sends no
provider, so on the default route (the route every chat turn takes) the chip is
silent no matter who answered.

WHAT BOTH LANES NOW RETURN (POST /chat response dict, POST /chat/stream done
frame): ``route = {requested, provider, model, reason}`` — additive; top-level
``provider``/``model`` are untouched for existing clients.

THE SEMANTICS THIS FILE PINS (the one place they are documented as contract):

* ``route.requested`` is the provider the caller EXPLICITLY asked for, and is
  ``""`` on the DEFAULT route. Chat sends no provider, so "" is chat's normal
  value; the default's NAME is not echoed into ``requested`` because it is
  already in ``route.provider`` — duplicating it would make "user picked X"
  and "default happened to be X" indistinguishable, which is the exact
  ambiguity this feature removes.
* ``route.reason`` is one of ``explicit`` / ``default`` / ``failover`` /
  ``prompted-tools`` / ``auto-tier`` / ``local-oracle`` / ``mock`` — the same
  strings ``provider.routed`` always carried, threaded onto the result.
* ``reason == "mock"`` WHENEVER the mock served, even though the resolver's
  own reason was "default" (fresh install) or "explicit" (a chosen mock). On a
  fresh install the mock IS the default, i.e. the majority case, and a
  scripted answer must never be allowed to look ordinary. Invariant:
  ``route.reason == "mock"`` iff ``route.provider == "mock"``.

AND THE OTHER HALF: ``documents`` now reports EVERY file a turn created — the
``ToolResult.created_paths`` merge — not just the ``_DOC_WRITING_TOOLS``
``data.path`` writes, in BOTH lanes. A repl-written file used to reach the
disk and never reach the preview rail.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import (
    LLMAdapter,
    LLMMessage,
    LLMResponse,
    ProviderError,
    ToolCall,
)
from iron_jarvis.providers.router import ModelRouter
from iron_jarvis.tools.base import Reversibility, Tool, ToolResult

# --------------------------------------------------------------------------- #
# Fakes + wiring (the test_no_mock_fabrication_v1162 patterns: build the real
# app, then shadow manager.available/get on the INSTANCE so nothing depends on
# which CLIs are installed on the machine running the suite).
# --------------------------------------------------------------------------- #


class _RealAdapter(LLMAdapter):
    """Stands in for a CONNECTED real provider. Subclasses LLMAdapter so it
    inherits the default single-chunk ``stream`` and full default capabilities
    (tool_use=True) — the stream lane and the capability checks see a normal
    API-class adapter. Optionally emits ONE tool call on its first completion,
    then answers with text (so the chat tool loop terminates)."""

    def __init__(self, provider="acme", model="acme-1", tool_call: ToolCall | None = None):
        self.provider = provider
        self.model = model
        self._tool_call = tool_call
        self.rounds = 0

    async def complete(self, *, system, messages, tools):
        self.rounds += 1
        if self._tool_call is not None and self.rounds == 1:
            return LLMResponse(text="", tool_calls=[self._tool_call], usage={})
        return LLMResponse(text=f"answered by {self.provider}", tool_calls=[], usage={})


class _FailingAdapter(_RealAdapter):
    def __init__(self, provider, exc):
        super().__init__(provider=provider, model=f"{provider}-m")
        self._exc = exc

    async def complete(self, *, system, messages, tools):
        self.rounds += 1
        raise self._exc


class _CreatorTool(Tool):
    """A tool that learns its output filename only from doing the work — the
    repl-tool shape: it reports the file via ``created_paths`` (absolute), NOT
    via ``data.path``, and it is not in ``_DOC_WRITING_TOOLS``."""

    name = "fake_creator"
    description = "creates a file it can only name afterwards"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY  # no undo capture noise in the test

    def __init__(self, target: Path):
        self._target = target

    async def execute(self, args, ctx):
        self._target.parent.mkdir(parents=True, exist_ok=True)
        self._target.write_text("made", encoding="utf-8")
        return ToolResult(
            ok=True,
            output="made a file",
            created_paths=[str(self._target)],
        )


def _wire_real(client, adapter, *, default=False):
    """Connect ``adapter`` as a REAL provider on the live platform. With
    ``default=True`` it also becomes the default provider (the route chat
    takes when the body names none)."""
    platform = client.app.state.platform
    if default:
        platform.config.default_provider = adapter.provider
    real_get = platform.providers.get
    platform.providers.available = lambda name: name in (adapter.provider, "mock")

    def _get(p, m=None):
        return adapter if p == adapter.provider else real_get(p, m)

    platform.providers.get = _get
    return platform


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _sse_events(raw: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in raw.split("\n\n"):
        ev, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event: "):
                ev = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if ev is not None:
            events.append((ev, data or {}))
    return events


def _done_frame(raw: str) -> dict:
    done = [p for e, p in _sse_events(raw) if e == "done"]
    assert done, f"no done frame in stream: {raw[:400]}"
    return done[0]


_MSGS = {"messages": [{"role": "user", "content": "hi"}]}


# --------------------------------------------------------------------------- #
# (1) DEFAULT ROUTE — the route every chat turn takes (chat sends no provider).
# --------------------------------------------------------------------------- #
def test_default_route_discloses_default_with_empty_requested(tmp_path):
    """SEMANTICS PINNED HERE: on the default route ``requested`` is ``""`` (the
    caller asked for nothing by name) and ``reason`` is ``"default"``; the
    provider that answered is in ``route.provider``. This is the turn that was
    previously silent in the UI."""
    with _client(tmp_path) as client:
        _wire_real(client, _RealAdapter(), default=True)
        r = client.post("/chat", json=_MSGS)
    assert r.status_code == 200, r.text
    assert r.json()["route"] == {
        "requested": "",
        "provider": "acme",
        "model": "acme-1",
        "reason": "default",
    }


# --------------------------------------------------------------------------- #
# (2) EXPLICIT PICK.
# --------------------------------------------------------------------------- #
def test_explicit_pick_discloses_explicit_and_names_the_ask(tmp_path):
    with _client(tmp_path) as client:
        _wire_real(client, _RealAdapter())  # NOT the default — an explicit ask
        r = client.post("/chat", json={**_MSGS, "provider": "acme"})
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route["requested"] == "acme"
    assert route["reason"] == "explicit"
    assert route["provider"] == "acme"


# --------------------------------------------------------------------------- #
# (3) MOCK DEFAULT (fresh install) — the mock must be UNMISTAKABLE.
# --------------------------------------------------------------------------- #
def test_fresh_install_mock_default_is_disclosed_as_mock(tmp_path):
    """A fresh install ships default_provider='mock'. The resolver's reason is
    'default' — the majority case — which is precisely where a scripted answer
    hides. The disclosed reason is therefore 'mock', and route.provider makes
    it independently checkable."""
    with _client(tmp_path) as client:  # untouched app: mock IS the default
        r = client.post("/chat", json=_MSGS)
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route["provider"] == "mock"
    assert route["reason"] == "mock"
    assert route["requested"] == ""  # still the default route


def test_an_explicitly_chosen_mock_still_says_mock(tmp_path):
    """reason == 'mock' iff provider == 'mock' — even for an explicit pick.
    ``requested`` still records the ask, so the two mock cases stay tellable."""
    with _client(tmp_path) as client:
        r = client.post("/chat", json={**_MSGS, "provider": "mock"})
    assert r.status_code == 200, r.text
    route = r.json()["route"]
    assert route == {
        "requested": "mock",
        "provider": "mock",
        "model": route["model"],  # whatever id the mock reports
        "reason": "mock",
    }


# --------------------------------------------------------------------------- #
# (4) THE STREAM LANE'S done FRAME CARRIES THE IDENTICAL route OBJECT.
# --------------------------------------------------------------------------- #
def test_stream_done_frame_route_is_identical_to_post_chat(tmp_path):
    """The streaming lane is the one users watch; a disclosure that exists only
    on POST /chat is a disclosure most turns never get. Byte-identical object,
    default route."""
    with _client(tmp_path) as client:
        _wire_real(client, _RealAdapter(), default=True)
        flat = client.post("/chat", json=_MSGS)
        streamed = client.post("/chat/stream", json=_MSGS)
    assert flat.status_code == 200 and streamed.status_code == 200
    done = _done_frame(streamed.text)
    assert done["route"] == flat.json()["route"]
    assert done["route"] == {
        "requested": "",
        "provider": "acme",
        "model": "acme-1",
        "reason": "default",
    }


def test_stream_done_frame_discloses_the_mock_default_too(tmp_path):
    with _client(tmp_path) as client:  # fresh install: mock default
        r = client.post("/chat/stream", json=_MSGS)
    done = _done_frame(r.text)
    assert done["route"]["provider"] == "mock"
    assert done["route"]["reason"] == "mock"
    assert done["route"]["requested"] == ""


# --------------------------------------------------------------------------- #
# (5) created_paths REACH `documents` IN BOTH LANES.
# --------------------------------------------------------------------------- #
def _tool_call():
    return ToolCall(id="call-1", name="fake_creator", arguments={})


def test_created_paths_reach_documents_on_post_chat(tmp_path):
    target = tmp_path / "out" / "report_final.md"
    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_CreatorTool(target))
        r = client.post("/chat", json={**_MSGS, "tools": ["fake_creator"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tools_used"] == ["fake_creator"], body
    assert str(target) in body["documents"], (
        f"created_paths never reached documents: {body['documents']}"
    )
    assert Path(str(target)).is_absolute()
    assert target.is_file()  # the disclosure describes a real file


def test_created_paths_reach_documents_on_the_stream_lane(tmp_path):
    target = tmp_path / "out" / "stream_made.md"
    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_CreatorTool(target))
        r = client.post("/chat/stream", json={**_MSGS, "tools": ["fake_creator"]})
    done = _done_frame(r.text)
    assert done["tools_used"] == ["fake_creator"], done
    assert str(target) in done["documents"], (
        f"created_paths never reached the stream lane's documents: {done['documents']}"
    )


def test_created_paths_are_deduped_and_keep_order(tmp_path):
    """Two rounds of the same creator must not produce duplicate chips."""
    target = tmp_path / "out" / "once.md"

    class _TwoCalls(_RealAdapter):
        async def complete(self, *, system, messages, tools):
            self.rounds += 1
            if self.rounds <= 2:
                return LLMResponse(
                    text="", tool_calls=[ToolCall(id=f"c{self.rounds}",
                                                  name="fake_creator",
                                                  arguments={})], usage={}
                )
            return LLMResponse(text="done", tool_calls=[], usage={})

    with _client(tmp_path) as client:
        platform = _wire_real(client, _TwoCalls(), default=True)
        platform.registry.register(_CreatorTool(target))
        r = client.post("/chat", json={**_MSGS, "tools": ["fake_creator"]})
    assert r.status_code == 200, r.text
    docs = r.json()["documents"]
    assert docs == [str(target)], docs


# --------------------------------------------------------------------------- #
# (6) EXISTING CONSUMERS UNBROKEN: top-level provider/model stay put.
# --------------------------------------------------------------------------- #
def test_top_level_provider_and_model_are_untouched(tmp_path):
    with _client(tmp_path) as client:
        _wire_real(client, _RealAdapter(), default=True)
        flat = client.post("/chat", json=_MSGS).json()
        done = _done_frame(client.post("/chat/stream", json=_MSGS).text)
    assert flat["provider"] == "acme" and flat["model"] == "acme-1"
    assert done["provider"] == "acme" and done["model"] == "acme-1"


# --------------------------------------------------------------------------- #
# (7) ROUTER-LEVEL: a FAILOVER answer says so — in both router lanes.
# --------------------------------------------------------------------------- #
class _Mgr:
    def __init__(self, adapters, available):
        self.adapters = adapters
        self._avail = set(available)

    def available(self, p):
        return p in self._avail

    def has_available_api_provider(self):
        return True

    def get(self, p, m=None):
        return self.adapters[p]


@pytest.fixture()
def _no_sleep(monkeypatch):
    """Instant same-adapter retry backoff (the test_router_reliability trick)."""
    import iron_jarvis.providers.router as rmod

    async def _instant(_):
        return None

    monkeypatch.setattr(rmod.asyncio, "sleep", _instant)


def _failover_router():
    anth = _FailingAdapter("anthropic", ProviderError("overloaded", status_code=429))
    grok = _RealAdapter("grok-cli", "grok-m")
    mgr = _Mgr(
        {"anthropic": anth, "grok-cli": grok, "mock": _RealAdapter("mock", "mock-m")},
        available={"anthropic", "grok-cli", "mock"},
    )
    return ModelRouter(mgr, "anthropic", EventBus())


def test_complete_failover_is_disclosed(_no_sleep):
    """The user asked for anthropic; grok-cli answered. requested must keep the
    ASK and reason must say 'failover' — this is the disclosure that stops a
    silent provider swap from reading as the pick having answered."""
    r = _failover_router()
    res = asyncio.run(
        r.complete(provider="anthropic", system="",
                   messages=[LLMMessage("user", "hi")], tools=[])
    )
    assert res.provider == "grok-cli"
    assert res.requested == "anthropic"
    assert res.reason == "failover"


@pytest.mark.asyncio
async def test_stream_final_frame_discloses_failover(_no_sleep):
    """MIRROR: the stream lane's final frame carries the same requested/reason
    the RouteResult does (complete() and stream() are lock-step by repo rule)."""
    r = _failover_router()
    frames = []
    async for f in r.stream(provider="anthropic", system="",
                            messages=[LLMMessage("user", "hi")], tools=[]):
        frames.append(f)
    finals = [f for f in frames if f.get("type") == "final"]
    assert finals, frames
    assert finals[0]["provider"] == "grok-cli"
    assert finals[0]["requested"] == "anthropic"
    assert finals[0]["reason"] == "failover"


def test_route_result_old_constructor_shape_still_works():
    """Deliverable-1 guard: every pre-existing 3-positional constructor call
    (and test stub) keeps working, with the documented safe defaults."""
    from iron_jarvis.providers.router import RouteResult

    res = RouteResult(LLMResponse(text="x"), "acme", "acme-1")
    assert res.requested == ""
    assert res.reason == "default"


# =========================================================================== #
# REVIEWER ADDITIONS (Pair A verifier). Everything below hardens the frozen
# wire contract: route:{requested,provider,model,reason}, ""=no explicit pick,
# mock-wins — plus the created_paths merge rules the doer left implicit.
# =========================================================================== #


# --------------------------------------------------------------------------- #
# (8) STREAM/POST PARITY, extended: explicit pick, failover, mock — the
# identity test above covered only default-served.
# --------------------------------------------------------------------------- #
def test_parity_explicit_pick_both_lanes_identical(tmp_path):
    with _client(tmp_path) as client:
        _wire_real(client, _RealAdapter())  # NOT the default — explicit ask
        flat = client.post("/chat", json={**_MSGS, "provider": "acme"})
        streamed = client.post("/chat/stream", json={**_MSGS, "provider": "acme"})
    assert flat.status_code == 200 and streamed.status_code == 200
    done = _done_frame(streamed.text)
    assert done["route"] == flat.json()["route"]
    assert done["route"] == {
        "requested": "acme",
        "provider": "acme",
        "model": "acme-1",
        "reason": "explicit",
    }


def test_parity_mock_fresh_install_both_lanes_identical(tmp_path):
    with _client(tmp_path) as client:  # untouched app: mock IS the default
        flat = client.post("/chat", json=_MSGS)
        streamed = client.post("/chat/stream", json=_MSGS)
    assert flat.status_code == 200 and streamed.status_code == 200
    done = _done_frame(streamed.text)
    assert done["route"] == flat.json()["route"]
    assert done["route"]["reason"] == "mock"
    assert done["route"]["provider"] == "mock"
    assert done["route"]["requested"] == ""


def _wire_failover_pair(client):
    """DEFAULT provider fails mid-call (transient 429); grok-cli is connected.
    The real router must retry, then fail sideways — and BOTH lanes must
    disclose the adapter that ACTUALLY answered, not the one that failed."""
    platform = client.app.state.platform
    anth = _FailingAdapter("anthropic", ProviderError("overloaded", status_code=429))
    grok = _RealAdapter("grok-cli", "grok-m")
    platform.config.default_provider = "anthropic"
    # Zero per-request budget: the same-adapter retry is SKIPPED when its
    # backoff would blow the deadline, so the transient failure fails over
    # immediately — no asyncio.sleep patch (patching sleep globally makes the
    # app's background loops busy-spin and hangs the TestClient).
    platform.router._deadline_s = 0.0
    real_get = platform.providers.get
    platform.providers.available = (
        lambda name: name in ("anthropic", "grok-cli", "mock")
    )

    def _get(p, m=None):
        if p == "anthropic":
            return anth
        if p == "grok-cli":
            return grok
        return real_get(p, m)

    platform.providers.get = _get
    return platform


def test_parity_failover_both_lanes_identical(tmp_path):
    """Mid-call failure on the DEFAULT route: route.provider must name the
    adapter that ANSWERED (grok-cli), requested stays "" (nobody picked
    anthropic by name this turn), reason says failover — identically on POST
    /chat and the stream done frame. This is trap 2(b) proven at the lane
    level, not just the router level."""
    with _client(tmp_path) as client:
        _wire_failover_pair(client)
        flat = client.post("/chat", json=_MSGS)
        streamed = client.post("/chat/stream", json=_MSGS)
    assert flat.status_code == 200, flat.text
    done = _done_frame(streamed.text)
    assert done["route"] == flat.json()["route"]
    assert done["route"] == {
        "requested": "",
        "provider": "grok-cli",
        "model": "grok-m",
        "reason": "failover",
    }
    # Top-level provider/model tell the same story (old-client surface).
    assert flat.json()["provider"] == "grok-cli"
    assert done["provider"] == "grok-cli"


# --------------------------------------------------------------------------- #
# (9) ERROR PATHS DISCLOSE NOTHING: the v1.162.0 refusal raises BEFORE any
# RouteResult exists — no half-built route may leak into an error response.
# --------------------------------------------------------------------------- #
def test_unconnected_default_error_carries_no_route_object(tmp_path):
    with _client(tmp_path) as client:
        platform = client.app.state.platform
        platform.config.default_provider = "ghost"
        platform.providers.available = lambda name: name == "mock"
        flat = client.post("/chat", json=_MSGS)
        streamed = client.post("/chat/stream", json=_MSGS)
    assert flat.status_code == 502
    body = flat.json()
    assert "route" not in body
    assert "ghost" in body["detail"]
    events = _sse_events(streamed.text)
    assert not [p for e, p in events if e == "done"]  # no done frame at all
    errors = [p for e, p in events if e == "error"]
    assert errors and "ghost" in errors[0]["detail"]
    assert all("route" not in p for e, p in events)


# --------------------------------------------------------------------------- #
# (10) created_paths hygiene: the contract says ABSOLUTE; a lying tool's
# relative path must not reach `documents` (resolving it against a guessed
# base could disclose the WRONG file); and a FAILED tool's paths are not
# merged — the same rule the registry's journal applies
# (``created_paths=result.created_paths if result.ok else None``).
# --------------------------------------------------------------------------- #
class _LyingCreatorTool(Tool):
    """Returns one contract-violating RELATIVE path alongside a real absolute
    one — only the absolute may land in `documents`."""

    name = "fake_creator"  # reuse the armed name the fake adapter calls
    description = "reports a relative created path (contract violation)"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, target: Path):
        self._target = target

    async def execute(self, args, ctx):
        self._target.parent.mkdir(parents=True, exist_ok=True)
        self._target.write_text("made", encoding="utf-8")
        return ToolResult(
            ok=True,
            output="made files",
            created_paths=["relative_note.md", str(self._target), ""],
        )


class _FailingCreatorTool(Tool):
    """Writes a file, then FAILS — its created_paths claim rides an ok=False
    result and must not be merged (matches the registry journal convention)."""

    name = "fake_creator"
    description = "writes then fails"
    input_schema = {"type": "object", "properties": {}}
    reversibility = Reversibility.READONLY

    def __init__(self, target: Path):
        self._target = target

    async def execute(self, args, ctx):
        self._target.parent.mkdir(parents=True, exist_ok=True)
        self._target.write_text("half", encoding="utf-8")
        return ToolResult(
            ok=False,
            error="exploded after writing",
            created_paths=[str(self._target)],
        )


def test_relative_created_paths_are_dropped_in_both_lanes(tmp_path):
    target = tmp_path / "out" / "real_abs.md"
    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_LyingCreatorTool(target))
        flat = client.post("/chat", json={**_MSGS, "tools": ["fake_creator"]})
    assert flat.status_code == 200, flat.text
    assert flat.json()["documents"] == [str(target)], flat.json()["documents"]

    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_LyingCreatorTool(target))
        streamed = client.post(
            "/chat/stream", json={**_MSGS, "tools": ["fake_creator"]}
        )
    done = _done_frame(streamed.text)
    assert done["documents"] == [str(target)], done["documents"]


def test_failed_tools_created_paths_are_not_merged_in_both_lanes(tmp_path):
    target = tmp_path / "out" / "half_written.md"
    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_FailingCreatorTool(target))
        flat = client.post("/chat", json={**_MSGS, "tools": ["fake_creator"]})
    assert flat.status_code == 200, flat.text
    assert flat.json()["documents"] == [], flat.json()["documents"]
    assert flat.json()["tools_used"] == []      # a failed call is not "used"

    with _client(tmp_path) as client:
        platform = _wire_real(
            client, _RealAdapter(tool_call=_tool_call()), default=True
        )
        platform.registry.register(_FailingCreatorTool(target))
        streamed = client.post(
            "/chat/stream", json={**_MSGS, "tools": ["fake_creator"]}
        )
    done = _done_frame(streamed.text)
    assert done["tools_used"] == []
    assert done["documents"] == [], done["documents"]


# --------------------------------------------------------------------------- #
# (11) THE ROUTER IS AUTHORITATIVE over the stream lane's seed: a final frame
# reporting requested="" must OVERRIDE a non-empty seed. Pins the deliberate
# membership test (`"requested" in frame`) — a truthiness read would silently
# keep the seed and mask a router that reports differently.
# --------------------------------------------------------------------------- #
def test_stream_requested_from_router_overrides_the_seed(tmp_path, monkeypatch):
    from iron_jarvis.providers.adapters.base import LLMResponse as _Resp

    with _client(tmp_path) as client:
        platform = client.app.state.platform

        async def fake_stream(*, provider=None, model=None, system, messages,
                              tools, session_id=None, task_class=None):
            yield {"type": "text", "text": "hi"}
            yield {
                "type": "final",
                "response": _Resp(text="hi"),
                "provider": "zeta",
                "model": "z1",
                "requested": "",      # the router says: default route
                "reason": "default",
            }

        monkeypatch.setattr(platform.router, "stream", fake_stream)
        r = client.post("/chat/stream", json={**_MSGS, "provider": "acme"})
    done = _done_frame(r.text)
    # The seed was "acme" (the body's pick); the router's "" must win.
    assert done["route"]["requested"] == ""
    assert done["route"]["provider"] == "zeta"
    assert done["route"]["reason"] == "default"


# --------------------------------------------------------------------------- #
# (12) AUTO pseudo-provider: requested is "" when Auto is merely the DEFAULT,
# and "auto" when the caller explicitly picked Auto — the explicit pick is a
# real pick, and erasing it would conflate the two states this feature exists
# to distinguish. reason is "auto-tier" either way; provider names the REAL
# adapter that served.
# --------------------------------------------------------------------------- #
def _auto_router(default="auto"):
    grok = _RealAdapter("grok-cli", "grok-m")
    mgr = _Mgr(
        {"grok-cli": grok, "mock": _RealAdapter("mock", "mock-m")},
        available={"grok-cli"},
    )
    return ModelRouter(mgr, default, EventBus())


def test_auto_as_default_keeps_requested_empty():
    r = _auto_router(default="auto")
    res = asyncio.run(
        r.complete(system="", messages=[LLMMessage("user", "hi")], tools=[])
    )
    assert res.provider == "grok-cli"
    assert res.requested == ""            # nobody picked auto by name
    assert res.reason == "auto-tier"


def test_auto_picked_explicitly_is_recorded_as_the_ask():
    r = _auto_router(default="mock")
    res = asyncio.run(
        r.complete(provider="auto", system="",
                   messages=[LLMMessage("user", "hi")], tools=[])
    )
    assert res.provider == "grok-cli"
    assert res.requested == "auto"        # the pick the caller actually made
    assert res.reason == "auto-tier"
