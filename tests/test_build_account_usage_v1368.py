"""v1.368.0 (IJ harness Phase 5): the REAL adapters under a Build account pin.

* Claude: the REAL ``ClaudeCliAdapter`` through the v1.300.0 fake CLI and its
  REAL admission relay, against a loopback fake Anthropic. The relay's
  allow-listed ``anthropic-ratelimit-*`` response headers ride
  ``LLMResponse.extra["ratelimit_headers"]`` and reach Iron-Proxy's
  ``/finished`` (only when it advertises ``plan-usage``; never a cookie);
  a 429 under a pin is reported and NEVER rotated; another account's signed
  thinking that this account refuses is sent again ONCE without it.
* Codex: the REAL ``CodexNativeAdapter`` against the phase-2 fake app-server:
  the account's rate-limit snapshot rides ``/finished`` and ``/signal`` as
  Iron-Proxy's ``rateLimits``, and a parked session on another account is
  never resumed under a pin.
* The fake Iron-Proxy LEARNS from those reports the way Iron-Proxy 1b1ea71
  does, and ``GET /build/accounts``' row builder reads the result: plan and
  percent from Iron-Proxy's own answers.

Nothing reaches the network or the user's Iron-Proxy.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy import pin as P
from iron_jarvis.iron_proxy.client import IronProxyClient
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.adapters.claude_native import frames as native_frames
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.cli_auth import CliAuthProbe
from tests.test_build_accounts_v1368 import bodies, picks
from tests.test_iron_proxy_accounts_v1301 import OK_EVENTS, Claude, _err_body, _homes

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from fake_iron_proxy_v1301 import FEATURES_PHASE5, FakeIronProxy  # noqa: E402

PINNED_ONLY = ("executor-v1", "pick-profile", "pick-pinned")


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    import os
    import socket

    from iron_jarvis.providers import cli_auth

    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in ("CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    probe = CliAuthProbe(which=lambda b: None)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(transport, "DEFAULT_PROBE", probe)
    monkeypatch.setattr(accounts, "_NOTED", set())
    real = socket.getaddrinfo

    def loopback_only(host, *args, **kw):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise socket.gaierror(11001, "test guard: no real network")
        return real(host, *args, **kw)

    monkeypatch.setattr(socket, "getaddrinfo", loopback_only)
    previous = accounts.set_client_source(lambda: None)
    yield
    accounts.set_client_source(previous)


def _proxy(tmp_path, homes: dict[str, str], provider: str = "anthropic", features=FEATURES_PHASE5):
    fake = FakeIronProxy(tmp_path / "ipd", features=list(features)).start()
    for pid, home in homes.items():
        fake.add_profile(provider, f"Account {pid}", id=pid, home=home)
    client = IronProxyClient(fake.url, fake.token)
    accounts.set_client_source(lambda: client)
    return fake


class Anthropic:
    """A loopback api.anthropic.com: ``answers`` is a queue of
    ``(status, body, headers)``; past it, 200 with ``ok_headers``."""

    def __init__(self, ok_headers: dict[str, str], answers: list | None = None) -> None:
        self.answers = list(answers or [])
        self.ok_headers = ok_headers
        self.hits = 0
        owner = self

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.hits += 1
                if owner.answers:
                    status, body, headers = owner.answers.pop(0)
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                for k, v in owner.ok_headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write("".join("data: " + json.dumps(e) + "\n\n" for e in OK_EVENTS).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


RESET_5H = int(time.time()) + 3 * 3600
RESET_7D = int(time.time()) + 4 * 86400
LIMIT_HEADERS = {
    "anthropic-ratelimit-unified-5h-utilization": "0.42",
    "anthropic-ratelimit-unified-5h-reset": str(RESET_5H),
    "anthropic-ratelimit-unified-7d-utilization": "0.09",
    "anthropic-ratelimit-unified-7d-reset": str(RESET_7D),
    "anthropic-ratelimit-unified-status": "allowed",
    "Set-Cookie": "session=SECRET-COOKIE",
    "x-other": "nope",
}


def _user(text="hi") -> LLMMessage:
    return LLMMessage(role="user", content=text)


def _pinned(pid: str, provider: str = "claude-cli", title: str = "") -> P.AccountPin:
    return P.AccountPin(profile_id=pid, ij_provider=provider, title=title or f"Account {pid}")


async def _run(adapter, messages, pin) -> object:
    token = P.set_pin(pin)
    try:
        return await adapter.complete(system="", messages=messages, tools=[])
    finally:
        P.reset_pin(token)


# --------------------------------------------------------------------------- #
# Claude
# --------------------------------------------------------------------------- #


async def test_claude_ratelimit_headers_reach_finished_and_iron_proxy_learns_the_percent(tmp_path, monkeypatch):
    homes = _homes(tmp_path, "A", "B")
    fake = _proxy(tmp_path, homes)
    up = Anthropic(LIMIT_HEADERS)
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto")
        resp = await _run(claude.adapter(up.url), [_user()], _pinned("A"))
        assert resp.text == "FIRST"
        assert claude.record["env"]["CLAUDE_CONFIG_DIR"] == homes["A"]
        # On the response: ONLY the allow-list (no cookie, nothing else).
        seen = resp.extra["ratelimit_headers"]
        assert seen["anthropic-ratelimit-unified-5h-utilization"] == "0.42"
        assert "set-cookie" not in seen and "x-other" not in seen
        [(pid, body)] = bodies(fake, "finished")
        assert pid == "A" and body["headers"] == seen
        assert "SECRET" not in json.dumps(fake.calls)
        # What Iron-Proxy learned is what the card shows: the window NEAREST
        # its limit (42% of 5 hours), from Iron-Proxy's own state.
        row = BA.row_of(fake.profiles["A"], fake.states["A"], vendor="claude", default_id="A")
        assert row["status"] == "usage"
        assert row["usage"] == {"percent": 42, "window": "5 hours", "window_mins": 300,
                                "resets_at": accounts.iso_utc(RESET_5H)}
        assert [p["profileId"] for p in picks(fake)] == ["A"]
    finally:
        up.stop()
        fake.stop()


async def test_an_older_iron_proxy_is_not_sent_the_headers(tmp_path, monkeypatch):
    homes = _homes(tmp_path, "A")
    fake = _proxy(tmp_path, homes, features=PINNED_ONLY)
    up = Anthropic(LIMIT_HEADERS)
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto")
        await _run(claude.adapter(up.url), [_user()], _pinned("A"))
        [(pid, body)] = bodies(fake, "finished")
        assert "headers" not in body and "rateLimits" not in body
    finally:
        up.stop()
        fake.stop()


async def test_a_claude_429_under_a_pin_is_reported_and_never_rotated(tmp_path, monkeypatch):
    homes = _homes(tmp_path, "A", "B")
    fake = _proxy(tmp_path, homes)
    up = Anthropic({}, answers=[(429, _err_body("rate_limit_error", "Rate limited"),
                                 {"retry-after": "120", **LIMIT_HEADERS})])
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto", native_text="API Error: 429")
        pin = _pinned("A")
        with pytest.raises(ProviderError) as info:
            await _run(claude.adapter(up.url), [_user()], pin)
        assert info.value.no_failover is True and "Account A" in str(info.value)
        assert up.hits == 1 and claude.record["env"]["CLAUDE_CONFIG_DIR"] == homes["A"], "no second account"
        assert pin.limit["kind"] == "rate" and pin.limit["resets_at"]
        [(pid, sig)] = bodies(fake, "signal")
        assert pid == "A" and sig["status"] == 429
        assert sig["headers"]["anthropic-ratelimit-unified-5h-utilization"] == "0.42"
        assert [p["profileId"] for p in picks(fake)] == ["A"]
    finally:
        up.stop()
        fake.stop()


def _thinking_turn() -> list[LLMMessage]:
    """A conversation whose earlier reply rides a native carrier WITH signed
    thinking — what another Claude account's call left (chat rotation)."""
    native = [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": "plan it", "signature": "SIG-FROM-ACCOUNT-A"},
        {"type": "text", "text": "earlier answer"},
    ]}]
    carrier = native_frames.carrier(native, "earlier answer", [])
    return [_user("first"), LLMMessage(role="assistant", content="earlier answer", raw_blocks=[carrier]),
            _user("second")]


async def test_another_accounts_thinking_refused_is_sent_again_once_without_it(tmp_path, monkeypatch):
    homes = _homes(tmp_path, "B")
    fake = _proxy(tmp_path, homes)
    refusal = _err_body("invalid_request_error",
                        "messages.1.content.0: Invalid `signature` in `thinking` block")
    up = Anthropic({}, answers=[(400, refusal, {})])
    sent: list[bool] = []
    real = native_frames.history_frames

    def spy(system, messages, names):
        sent.append(sc.carries_thinking(messages))
        return real(system, messages, names)

    monkeypatch.setattr(native_frames, "history_frames", spy)
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto", native_text="API Error: 400")
        pin = _pinned("B")
        resp = await _run(claude.adapter(up.url), _thinking_turn(), pin)
        assert resp.text == "FIRST" and up.hits == 2
        assert sent == [True, False], "the second request carried no earlier thinking"
        assert resp.extra.get("thinking_dropped") is True and pin.thinking_dropped is True
        assert bodies(fake, "signal") == [], "a refused signature is not the account's limit"
    finally:
        up.stop()
        fake.stop()


async def test_a_400_about_something_else_is_not_replayed(tmp_path, monkeypatch):
    up = Anthropic({}, answers=[(400, _err_body("invalid_request_error", "max_tokens: too large"), {})])
    try:
        claude = Claude(tmp_path, monkeypatch, "relay_auto", native_text="API Error: 400")
        with pytest.raises(ProviderError):
            await claude.adapter(up.url).complete(system="", messages=_thinking_turn(), tools=[])
        assert up.hits == 1
    finally:
        up.stop()


def test_without_native_blocks_keeps_every_turn_and_drops_only_carriers():
    msgs = _thinking_turn()
    foreign = {"type": "anthropic_native", "blocks": []}
    msgs[1].raw_blocks.append(foreign)
    out = sc.without_native_blocks(msgs)
    assert [(m.role, m.content) for m in out] == [(m.role, m.content) for m in msgs]
    assert out[1].raw_blocks == [foreign] and sc.carries_thinking(out) is False
    assert sc.carries_thinking(msgs) is True and msgs[1].raw_blocks[0]["type"] == "claude_cli_native"


# --------------------------------------------------------------------------- #
# Codex
# --------------------------------------------------------------------------- #


def test_the_codex_snapshot_is_sent_in_iron_proxys_shape():
    snap = {"planType": "plus", "limitId": "codex", "rateLimitReachedType": None,
            "primary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1900001000},
            "secondary": {"usedPercent": "x", "windowDurationMins": None, "resetsAt": None},
            "credits": {"hasCredits": True}, "observedAt": 1890000000}
    assert accounts.rate_limits_body(snap) == {
        "planType": "plus",
        "primary": {"usedPercent": 7, "windowDurationMins": 300, "resetsAt": 1900001000},
        "observedAt": 1890000000,
    }
    assert accounts.rate_limits_body({"primary": None}) is None
    assert accounts.rate_limits_body("nope") is None


async def test_codex_rate_limits_reach_finished_and_signal_and_a_pin_never_rotates(tmp_path, monkeypatch):
    from tests.test_codex_native_v1365 import Fake, _err

    homes = _homes(tmp_path, "X", "Y")
    fake = _proxy(tmp_path, homes, provider="openai")
    try:
        codex = Fake(tmp_path, monkeypatch, [{"steps": [{"text": "hi"}]}])
        resp = await _run(codex.adapter(), [_user()], _pinned("X", "codex-cli"))
        assert resp.text == "hi"
        [(pid, body)] = bodies(fake, "finished")
        assert pid == "X" and body["rateLimits"]["planType"] == "plus"
        assert body["rateLimits"]["primary"]["usedPercent"] == 7
        row = BA.row_of(fake.profiles["X"], fake.states["X"], vendor="codex", default_id="X")
        assert row["plan"] == "plus" and row["plan_label"] == "Plus" and row["usage"]["percent"] == 7
        # A usage limit under a pin: reported WITH the snapshot, never rotated.
        reset = int(time.time()) + 600
        snap = {"planType": "plus", "rateLimitReachedType": "rateLimitReached",
                "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": reset}}
        codex2 = Fake(tmp_path, monkeypatch, [{"steps": [{"rate_limits": snap},
                                                         _err("usageLimitExceeded", "You've hit your usage limit.")]}])
        pin = _pinned("X", "codex-cli")
        with pytest.raises(ProviderError) as info:
            await _run(codex2.adapter(), [_user("again")], pin)
        assert info.value.no_failover is True and pin.limit is not None
        [(pid, sig)] = bodies(fake, "signal")
        assert pid == "X" and sig["status"] == 429 and sig["rateLimits"]["primary"]["usedPercent"] == 100
        assert all(r["env"].get("CODEX_HOME") == homes["X"] for r in codex2.starts()), "never account Y"
        assert {p["profileId"] for p in picks(fake)} == {"X"}
    finally:
        from iron_jarvis.providers.adapters.codex_native import session as CS

        for s in CS.POOL.live():
            CS.POOL.forget(s)
            s.kill()
        fake.stop()
