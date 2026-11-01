"""v1.422.0 review follow-up (Wave 2 "Never lose work", B1 — the owner's
decision): a reply that STARTS on a local model server that is not answering
is refused AT ONCE, as v1.228.0 made it; the patience waits out that kind of
failure only once the reply has already had a round answered.

The router's honest "isn't connected / not answering" refusal for a LOCAL
primary — ``_refuse_if_dead``'s probe (unreachable or no answer), the
run-stage refusal of a transport that never answered, the breaker's open
circuit — was waited out from the very first model call: a reply asked of a
local box that is OFF showed "Your own endpoint is not answering. Trying
again in …" for about two minutes before the refusal v1.228.0 built to come
at once. Now the FIRST model call of a turn takes that refusal as it is (one
try, the refusal's own words, no "tried N times"); a later round — the box
went away mid-reply, a vLLM restart — still waits it out. A cloud or CLI
provider's passing failure keeps the patience from the first round.

Driven through the REAL router and the real custom adapter: a closed port for
"off", and a real OpenAI-compatible stand-in server for "answered, then went
away" (``tests/fixtures/fake_openai_endpoint_v1387``); the patience's waits
return at once.
"""

from __future__ import annotations

import json
import socket

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.schemas import ChatBody, ChatMessageBody
from iron_jarvis.harness import patience as P
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers import router as router_mod
from iron_jarvis.providers.adapters.base import ProviderError
from tests.test_build_patience_v14220 import _frames


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    seen: list[float] = []

    async def fake_sleep(seconds):
        seen.append(float(seconds))

    monkeypatch.setattr(P, "WAITS_S", P.DEFAULT_WAITS_S)
    monkeypatch.setattr(P, "SLEEP", fake_sleep)
    # The router's own same-adapter retries back off by a random amount.
    monkeypatch.setattr(router_mod.random, "uniform", lambda a, b: 0.0)
    return seen


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _platform(tmp_path, url: str):
    platform = build_platform(str(tmp_path / "home"))
    platform.config.custom_base_url = url
    platform.config.custom_model = "glm-5.3-flash"
    platform.providers.configure_local(custom_base_url=url, custom_model="glm-5.3-flash")
    return platform


def _body(ws, text="tidy the notes") -> ChatBody:
    return ChatBody(
        messages=[ChatMessageBody(role="user", content=text)],
        provider="custom", model="glm-5.3-flash", workspace_dir=str(ws), approval_mode="yolo",
    )


async def test_a_reply_that_starts_on_a_local_server_that_is_off_is_refused_at_once(tmp_path, waits):
    ws = tmp_path / "ws"
    ws.mkdir()
    platform = _platform(tmp_path, f"http://127.0.0.1:{_closed_port()}/v1")
    gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(ws), harness=True)
    frames = _frames([c async for c in gen])
    assert not [d for e, d in frames if e == P.FRAME], "the first call waited out a server that is off"
    assert waits == []
    err = [d for e, d in frames if e == "error"]
    assert err, frames[-3:]
    # The refusal's own words, never "tried N times".
    assert "isn't connected" in err[0]["detail"], err[0]
    assert "tried" not in err[0].get("fix", ""), err[0]


async def test_a_local_server_that_went_away_mid_reply_is_waited_out(tmp_path, waits):
    import threading

    from tests.fixtures.fake_openai_endpoint_v1387 import FakeEndpoint, calls_reply

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "notes.txt").write_text("a note\n", encoding="utf-8")
    asked = {"n": 0}
    srv = FakeEndpoint()

    def handler(body: dict):
        asked["n"] += 1
        if asked["n"] == 1:
            return calls_reply(("read_file", {"path": "notes.txt"}))
        # A vLLM restart: the box answered round one, then went away — this
        # request's connection drops and the port closes.
        threading.Thread(target=srv.__exit__, args=(None, None, None), daemon=True).start()
        raise ConnectionAbortedError("the box went away")

    srv.handler = handler
    srv.__enter__()
    try:
        platform = _platform(tmp_path, srv.url)
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, _body(ws), harness=True)
        frames = _frames([c async for c in gen])
    finally:
        srv.__exit__(None, None, None)
    assert [e for e, _ in frames if e == "error"] == ["error"], frames[-3:]
    # Round one was answered (its step ran)...
    assert [d for e, d in frames if e == "tool_call" and d.get("status") == "finished" and d.get("ok")]
    # ...so the round after it waits the box out.
    waiting = [d for e, d in frames if e == P.FRAME and d.get("state") == "waiting"]
    assert [d["attempt"] for d in waiting] == [2, 3, 4, 5], waiting
    assert waits == [5.0, 15.0, 30.0, 60.0]
    err = [d for e, d in frames if e == "error"][0]
    assert "Iron Jarvis tried 5 times" in err.get("fix", ""), err


def test_the_rule_reads_the_router_local_refusal_only_at_a_first_call():
    """``RoundPatience(first_call=True)`` gives up at once on the router's
    local "not answering" refusal (unreachable / no answer / breaker open);
    a later round waits it out; a CLOUD provider's passing failure is waited
    out from the first call."""
    def local(kind: str) -> ProviderError:
        err = ProviderError("custom isn't connected right now")
        err.kind = kind  # type: ignore[attr-defined]
        err.failed_provider = "custom"  # type: ignore[attr-defined]
        if kind == "cooldown":
            err.retry_in = 20.0  # type: ignore[attr-defined]
        return err

    for kind in ("unreachable", "timeout", "cooldown"):
        assert P.RoundPatience(first_call=True).next_wait(local(kind)) is None, kind
        assert P.RoundPatience(first_call=False).next_wait(local(kind)) is not None, kind
    # A fleet node is local too.
    fleet = local("unreachable")
    fleet.failed_provider = "fleet-dgx"  # type: ignore[attr-defined]
    assert P.RoundPatience(first_call=True).next_wait(fleet) is None
    # A cloud provider's passing failures: waited out from the first call.
    cloud = ProviderError("anthropic API error 529: overloaded_error", status_code=529)
    assert P.RoundPatience(first_call=True).next_wait(cloud) is not None
    # A CLOUD primary whose breaker is open (another reply tripped it) keeps
    # the patience from the first call: it waits out the cooldown.
    open_cloud = local("cooldown")
    open_cloud.failed_provider = "anthropic"  # type: ignore[attr-defined]
    assert P.RoundPatience(first_call=True).next_wait(open_cloud) is not None
    cut = ProviderError("anthropic: connection reset")
    cut.kind = "interrupted"  # type: ignore[attr-defined]
    cut.failed_provider = "anthropic"  # type: ignore[attr-defined]
    assert P.RoundPatience(first_call=True).next_wait(cut) is not None
    # A local server that ANSWERED with a passing error (a proxy's 503) is a
    # server that is up: waited out from the first call.
    up = ProviderError("custom API error 503: unavailable", status_code=503)
    up.failed_provider = "custom"  # type: ignore[attr-defined]
    assert P.RoundPatience(first_call=True).next_wait(up) is not None


async def test_both_lanes_pass_the_first_call(tmp_path, waits):
    """Lock-step: the POST lane refuses a local server that is off at once
    too (one try)."""
    from iron_jarvis.daemon.chat_turn import ChatTurnFailed, run_chat_turn

    ws = tmp_path / "ws"
    ws.mkdir()
    platform = _platform(tmp_path, f"http://127.0.0.1:{_closed_port()}/v1")
    with pytest.raises(ChatTurnFailed) as caught:
        await run_chat_turn(platform, BUILTIN_PERSONAS, _body(ws), harness=True)
    assert waits == []
    assert "tried" not in json.dumps(caught.value.body())
