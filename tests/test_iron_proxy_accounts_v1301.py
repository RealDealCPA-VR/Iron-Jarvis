"""v1.301.0 — the subscription CLIs run AS the account Iron-Proxy picks.

Iron-Proxy is the account manager; Iron Jarvis keeps its own adapters and
borrows one account per call (``iron_proxy/accounts.py``): the account's
vendor-CLI home rides the CHILD's environment, a limit is reported (Iron-Proxy
parks the account) and the call is retried as the next account of the SAME
provider only while nothing has reached the caller; a success is reported with
its usage. Iron-Proxy off / no account for the provider -> exactly as before.

Every claude case drives the REAL ``ClaudeCliAdapter`` through the v1.300.0
fake CLI (``fixtures/fake_claude_v1300.py``, via a shim — the process tree an
npm ``claude.cmd`` has) and its real admission relay, pointed at a loopback
fake upstream that answers PER ACCOUNT (the fake CLI names its
``CLAUDE_CONFIG_DIR`` in a header). The env asserted is the env the CHILD
received. Codex runs a fake ``codex`` script the same way; Grok reads each
account's ``auth.json`` from its own home. The Iron-Proxy client is a fake
injected through ``accounts.set_client_source``. Nothing reaches the network.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from iron_jarvis.iron_proxy import accounts
from iron_jarvis.iron_proxy.client import IronProxyError
from iron_jarvis.providers import cli_auth
from iron_jarvis.providers.adapters import grok_cli
from iron_jarvis.providers.adapters import subprocess_cli as sc
from iron_jarvis.providers.adapters.base import LLMMessage, ProviderError
from iron_jarvis.providers.adapters.claude_native import admission as adm_mod
from iron_jarvis.providers.adapters.claude_native import transport
from iron_jarvis.providers.cli_auth import CliAuthProbe
from iron_jarvis.providers.router import is_transient_error

FAKE = Path(__file__).parent / "fixtures" / "fake_claude_v1300.py"
HOME_VARS = ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME")
VAR_OF = {"anthropic": "CLAUDE_CONFIG_DIR", "openai": "CODEX_HOME", "xai": "GROK_HOME"}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    for key in HOME_VARS:
        monkeypatch.delenv(key, raising=False)
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
    yield probe
    accounts.set_client_source(previous)


class FakeIronProxy:
    """Iron-Proxy's external-executor API, in memory. ``pick`` returns the
    first unparked account in order; ``signal`` parks by a small classifier
    standing in for Iron-Proxy's own; every call records its thread."""

    def __init__(self, provider: str, homes: dict[str, str], *, park=None, reset_at: str | None = None):
        self.provider = provider
        self.homes = dict(homes)
        self.order = list(homes)
        self.parked: dict[str, str] = {}
        self.picks: list[str] = []
        self.signals: list[dict] = []
        self.done: list[dict] = []
        self.threads: list[int] = []
        self.park = park or self._classify
        self.reset_at = reset_at or (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self.fail: dict[str, IronProxyError] = {}
        self.extra_set: dict[str, str] = {}
        self.unset = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]

    @staticmethod
    def _classify(status, headers, text) -> str | None:
        if status in (401,) or (status == 403 and "authentication" in (text or "").lower()):
            return "auth-expired"
        if status in (429, 503, 529):
            return "rate-limit"
        t = (text or "").lower()
        if "not logged in" in t:
            return "auth-expired"
        if "usage limit" in t:
            return "quota-exhausted"
        return None

    def pick(self, provider, lane="cli"):
        self.threads.append(threading.get_ident())
        if "pick" in self.fail:
            raise self.fail["pick"]
        assert provider == self.provider and lane == "cli"
        free = [p for p in self.order if p not in self.parked]
        if not free:
            if self.parked and all(k == "auth-expired" for k in self.parked.values()):
                last = list(self.parked)[-1]
                raise IronProxyError("AUTH_REQUIRED", "This account needs to log in again.", status=401,
                                     details={"profileId": last, "title": f"Title {last}"})
            raise IronProxyError("ALL_PROFILES_EXHAUSTED", "Every account is parked.", status=429,
                                 details={"provider": provider, "resetAt": self.reset_at,
                                          "earliestResetAt": self.reset_at, "tried": list(self.order)})
        pid = free[0]
        self.picks.append(pid)
        env_set = {VAR_OF[provider]: self.homes[pid], **self.extra_set}
        return {"profile": {"id": pid, "title": f"Title {pid}", "provider": provider},
                "env": {"set": env_set, "unset": list(self.unset)}}

    def signal(self, pid, status=None, headers=None, text=None):
        self.threads.append(threading.get_ident())
        self.signals.append({"id": pid, "status": status, "headers": headers, "text": text})
        if "signal" in self.fail:
            raise self.fail["signal"]
        kind = self.park(status, headers, text)
        if kind is None:
            return {"parked": False}
        self.parked[pid] = kind
        return {"parked": True, "signal": {"kind": kind}, "state": {}}

    def finished(self, pid, usage=None, duration_ms=None, model=None):
        self.threads.append(threading.get_ident())
        self.done.append({"id": pid, "usage": usage, "duration_ms": duration_ms, "model": model})
        if "finished" in self.fail:
            raise self.fail["finished"]
        return {"ok": True}


def _use(client) -> None:
    accounts.set_client_source(lambda: client)


def _shim(tmp_path: Path, script: Path, name: str) -> str:
    if os.name == "nt":
        shim = tmp_path / f"{name}.cmd"
        shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return str(shim)


def _user(text: str = "hi") -> LLMMessage:
    return LLMMessage(role="user", content=text)


def _homes(tmp_path: Path, *names: str) -> dict[str, str]:
    out = {}
    for n in names:
        home = tmp_path / f"home-{n}"
        home.mkdir()
        out[n] = str(home)
    return out


def _err_body(kind: str, message: str) -> bytes:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}}).encode()


OK_EVENTS = [
    {"type": "message_start", "message": {"id": "up", "role": "assistant", "model": "m", "content": [],
                                          "usage": {"input_tokens": 100, "output_tokens": 2,
                                                    "cache_read_input_tokens": 50,
                                                    "cache_creation_input_tokens": 0}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "FIRST"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    {"type": "message_stop"},
]


class AccountUpstream:
    """A loopback api.anthropic.com that answers by the account the fake CLI
    names (``X-Fake-Config-Dir``): ``plan[home] = (status, body, headers)``;
    a home not in the plan (or the default login) gets a 200 "FIRST"."""

    def __init__(self, plan: dict[str, tuple[int, bytes, dict[str, str]]]):
        self.plan = plan
        self.hits: list[str] = []
        owner = self

        class Peer(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                home = self.headers.get("X-Fake-Config-Dir", "")
                owner.hits.append(home)
                status, body, headers = owner.plan.get(home, (200, b"", {}))
                if status != 200:
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
                self.end_headers()
                self.wfile.write("".join("data: " + json.dumps(e) + "\n\n" for e in OK_EVENTS).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Peer)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def upstream():
    made: list[AccountUpstream] = []

    def make(plan) -> AccountUpstream:
        up = AccountUpstream(plan)
        made.append(up)
        return up

    yield make
    for up in made:
        up.stop()


class Claude:
    def __init__(self, tmp_path: Path, monkeypatch, scenario: str = "relay_auto", **knobs: str) -> None:
        self.shim = _shim(tmp_path, FAKE, "claude")
        self.record_path = tmp_path / "record.json"
        monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", scenario)
        monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(self.record_path))
        for key, value in knobs.items():
            monkeypatch.setenv(f"FAKE_CLAUDE_{key.upper()}", value)

    def adapter(self, upstream_url: str | None = None) -> sc.ClaudeCliAdapter:
        env = dict(os.environ, ANTHROPIC_BASE_URL=upstream_url) if upstream_url else None
        return sc.make_claude_cli(which=lambda _b: self.shim, model="claude-sonnet-5", env=env)

    @property
    def record(self) -> dict:
        return json.loads(self.record_path.read_text(encoding="utf-8"))


async def _stream_texts(adapter) -> tuple[list[str], object]:
    texts, final = [], None
    async for frame in adapter.stream(system="", messages=[_user()], tools=[]):
        if frame["type"] == "text":
            texts.append(frame["text"])
        elif frame["type"] == "final":
            final = frame["response"]
    return texts, final


LIMIT_HEADERS = {
    "retry-after": "120",
    "anthropic-ratelimit-unified-reset": "2026-10-03T22:05:00Z",
    "Set-Cookie": "session=SECRET-COOKIE",
    "x-other": "nope",
}


# --------------------------------------------------------------------------- #
# 1. off = exactly as before
# --------------------------------------------------------------------------- #
def _normalized(record: dict) -> tuple[list[str], dict]:
    argv = record["argv"]
    root = str(Path(argv[argv.index("--settings") + 1]).parent)
    env = {k: v for k, v in record["env"].items() if k != "ANTHROPIC_BASE_URL"}
    return [a.replace(root, "<root>") for a in argv], env


async def test_off_or_no_account_runs_exactly_as_before(tmp_path, monkeypatch):
    fake = Claude(tmp_path, monkeypatch, "text")
    seen = []
    # (a) the LIVE seam (service.current()), with Iron-Proxy off: the service
    # answers no client. Patched here so a service another test left behind in
    # this worker can never be picked up.
    from iron_jarvis.iron_proxy import service as svc_mod

    class _Off:
        def client(self):
            asked.append(True)
            return None

    asked: list[bool] = []
    monkeypatch.setattr(svc_mod, "current", lambda: _Off())
    accounts.set_client_source(None)
    resp = await fake.adapter().complete(system="s", messages=[_user()], tools=[])
    seen.append(_normalized(fake.record))
    assert asked == [True], "the adapter consulted the live service"
    # (b) Iron-Proxy on, but no account for this provider (NO_PROFILE)
    client = FakeIronProxy("anthropic", {})
    client.fail["pick"] = IronProxyError("NO_PROFILE", "No enabled profile.", status=404)
    _use(client)
    resp_b = await fake.adapter().complete(system="s", messages=[_user()], tools=[])
    seen.append(_normalized(fake.record))
    # (c) Iron-Proxy on, but disabled for leasing (the service answers no client)
    accounts.set_client_source(lambda: None)
    resp_c = await fake.adapter().complete(system="s", messages=[_user()], tools=[])
    seen.append(_normalized(fake.record))
    _use(client)
    # (d) the picked account is not a CLI account (env null): Iron Jarvis keeps its route
    client.fail.pop("pick")
    client.homes, client.order = {"K": ""}, ["K"]
    real_pick = client.pick
    client.pick = lambda provider, lane="cli": {**real_pick(provider, lane), "env": None}
    resp_d = await fake.adapter().complete(system="s", messages=[_user()], tools=[])
    seen.append(_normalized(fake.record))
    assert seen[0] == seen[1] == seen[2] == seen[3]
    assert "CLAUDE_CONFIG_DIR" not in fake.record["env"]
    assert resp.text == resp_b.text == resp_c.text == resp_d.text == "Hello"
    assert client.signals == [] and client.done == []
    assert len(client.threads) == 2  # asked twice, never told anything


async def test_a_refused_control_token_is_said_never_a_silent_default_login(tmp_path, monkeypatch):
    """AUTH_REQUIRED with no account named = Iron-Proxy refused OUR token. It is
    running and may hold accounts: say so, never run on the default login."""
    fake = Claude(tmp_path, monkeypatch, "text")
    client = FakeIronProxy("anthropic", {})
    client.fail["pick"] = IronProxyError("AUTH_REQUIRED", "Invalid token.", status=401)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert str(info.value) == (
        "claude-cli: Iron-Proxy refused Iron Jarvis's access, so it cannot say which Claude account "
        "to use — turn Iron-Proxy off and on again on the Connections page."
    )
    assert not info.value.transient
    assert not fake.record_path.exists(), "nothing ran"


# --------------------------------------------------------------------------- #
# 2. a lease: the CHILD runs as the account, success is reported with usage
# --------------------------------------------------------------------------- #
async def test_the_child_runs_as_the_account_and_success_is_reported(tmp_path, monkeypatch):
    fake = Claude(tmp_path, monkeypatch, "text")
    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("anthropic", homes)
    client.extra_set = {"ANTHROPIC_API_KEY": "sk-ant-SECRET"}  # never applied: only the home is
    client.unset = ["ANTHROPIC_API_KEY", "PATH", "CLAUDE_CODE_USE_BEDROCK"]  # PATH is never removed
    _use(client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-INHERITED")
    loop_thread = threading.get_ident()
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.text == "Hello"
    env = fake.record["env"]
    assert env["CLAUDE_CONFIG_DIR"] == homes["A"], "the account's home reached the child"
    assert "ANTHROPIC_API_KEY" not in env and "SECRET" not in json.dumps(fake.record)
    assert client.picks == ["A"] and client.signals == []
    [done] = client.done
    assert done["id"] == "A" and done["model"] and isinstance(done["duration_ms"], int)
    # usage: input_tokens is the TOTAL prompt (3 + 7 read + 11 written); Iron-Proxy
    # gets the part NOT read from cache and the cache read on its own.
    assert resp.usage["input_tokens"] == 21
    assert done["usage"] == {"inputTokens": 14, "outputTokens": 5, "cacheReadTokens": 7}
    assert all(t != loop_thread for t in client.threads), "blocking Iron-Proxy calls ran on the loop"


# --------------------------------------------------------------------------- #
# 3. a limit before any text: report it, run as the next account
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("streaming", [False, True])
async def test_a_429_before_text_moves_to_the_next_account(tmp_path, monkeypatch, upstream, streaming):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "Rate limited, try again"), LIMIT_HEADERS)})
    fake = Claude(tmp_path, monkeypatch, native_text="API Error: 429 Rate limited")
    client = FakeIronProxy("anthropic", homes)
    _use(client)
    loop_thread = threading.get_ident()
    if streaming:
        texts, resp = await _stream_texts(fake.adapter(up.url))
        assert texts == ["FIRST"], "the failed account streamed nothing; the next one streamed once"
    else:
        resp = await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert resp.text == "FIRST"
    assert up.hits == [homes["A"], homes["B"]], "account A first, then B — one upstream request each"
    assert fake.record["env"]["CLAUDE_CONFIG_DIR"] == homes["B"]
    [sig] = client.signals
    assert sig["id"] == "A" and sig["status"] == 429 and sig["text"] == "Rate limited, try again"
    # Exactly the allow-listed headers: never a cookie, never anything else.
    assert sig["headers"] == {"retry-after": "120", "anthropic-ratelimit-unified-reset": "2026-10-03T22:05:00Z"}
    assert [f["id"] for f in client.done] == ["B"]
    assert resp.usage["input_tokens"] == 150
    assert all(t != loop_thread for t in client.threads)


async def test_a_limit_after_text_is_reported_and_raised_without_a_switch(tmp_path, monkeypatch, upstream):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "Rate limited"), {"retry-after": "9"})})
    fake = Claude(tmp_path, monkeypatch, streamed="Hel", native_text="API Error: 429")
    client = FakeIronProxy("anthropic", homes)
    _use(client)
    texts: list[str] = []
    with pytest.raises(ProviderError) as info:
        async for frame in fake.adapter(up.url).stream(system="", messages=[_user()], tools=[]):
            if frame["type"] == "text":
                texts.append(frame["text"])
    assert texts == ["Hel"]
    assert info.value.status_code == 429 and info.value.retry_after == 9.0  # its own words
    assert info.value.no_failover is True, "an account limit never moves the call elsewhere"
    assert [s["id"] for s in client.signals] == ["A"], "the limit is still reported"
    assert up.hits == [homes["A"]] and client.picks == ["A"], "no mid-answer account switch"
    assert client.done == []


# --------------------------------------------------------------------------- #
# 4. every account at its limit: one plain sentence, never the default login
# --------------------------------------------------------------------------- #
def _local_words(iso: str) -> str:
    when = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    words = when.strftime("%I:%M %p").lstrip("0")
    if when.date() != datetime.now(timezone.utc).astimezone().date():
        words = when.strftime("%a ") + words
    return words


async def test_all_accounts_exhausted_is_one_plain_sentence(tmp_path, monkeypatch, upstream):
    homes = _homes(tmp_path, "A", "B")
    body = _err_body("rate_limit_error", "limited")
    up = upstream({homes["A"]: (429, body, {}), homes["B"]: (429, body, {})})
    fake = Claude(tmp_path, monkeypatch)
    reset = (datetime.now(timezone.utc) + timedelta(minutes=90)).replace(microsecond=0)
    client = StatefulIronProxy("anthropic", homes, reset_at=reset.isoformat().replace("+00:00", "Z"))
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    exc = info.value
    assert str(exc) == (
        f"claude-cli: All 2 Claude accounts in Iron-Proxy are at their request limit; "
        f"the first is free again at {_local_words(client.reset_at)}."
    )
    assert exc.no_failover is True
    assert exc.transient is False and not is_transient_error(exc)
    assert 85 * 60 <= exc.retry_after <= 90 * 60
    assert up.hits == [homes["A"], homes["B"]] and "" not in up.hits, "never the default login"
    assert [s["id"] for s in client.signals] == ["A", "B"]
    # Already all parked: the sentence comes BEFORE anything is spawned.
    fake.record_path.unlink()
    with pytest.raises(ProviderError, match="All 2 Claude accounts"):
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert not fake.record_path.exists()


async def test_the_one_account_needing_sign_in_is_named_and_the_provider_stays_signed_in(
        tmp_path, monkeypatch, _clean):
    """The CLI refuses locally ("Not logged in") as each account: Iron-Proxy marks
    each for sign-in; the user gets the per-account sentence, and the shared
    probe never says the whole claude provider is signed out."""
    fake = Claude(tmp_path, monkeypatch, "auth")
    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("anthropic", homes)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert str(info.value) == (
        'claude-cli: The Claude account "Title B" in Iron-Proxy needs to sign in again — sign it in '
        "on the Connections page (Iron-Proxy card), then try again."
    )
    assert not info.value.transient
    assert [s["text"] for s in client.signals] == ["Not logged in · Please run /login"] * 2
    assert _clean.status("claude") is None, "one account's sign-in is not the provider's"


async def test_a_401_parks_that_account_and_never_signs_the_provider_out(
        tmp_path, monkeypatch, upstream, _clean):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (401, _err_body("authentication_error", "OAuth token has expired."), {})})
    fake = Claude(tmp_path, monkeypatch, native_error="authentication_failed",
                  native_text="Failed to authenticate · Please run /login")
    client = FakeIronProxy("anthropic", homes)
    _use(client)
    resp = await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert resp.text == "FIRST"
    assert client.signals[0]["status"] == 401 and client.parked == {"A": "auth-expired"}
    assert _clean.status("claude") is None


# --------------------------------------------------------------------------- #
# 5. what is NOT a limit is not reported
# --------------------------------------------------------------------------- #
async def test_a_model_access_403_is_not_reported_as_a_limit(tmp_path, monkeypatch, upstream, _clean):
    homes = _homes(tmp_path, "A", "B")
    message = "Your organization does not have access to this model."
    up = upstream({homes["A"]: (403, _err_body("permission_error", message), {})})
    fake = Claude(tmp_path, monkeypatch, native_error="authentication_failed",
                  native_text=f"Failed to authenticate. API Error: 403 {message}")
    # A classifier that would park on ANY report: proves nothing was reported.
    client = FakeIronProxy("anthropic", homes, park=lambda *a: "rate-limit")
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert str(info.value).startswith("claude-cli: Anthropic refused the request (HTTP 403)")
    assert client.signals == [] and client.picks == ["A"] and up.hits == [homes["A"]]
    assert _clean.status("claude") is None


class RotatingIronProxy(FakeIronProxy):
    """Picks round-robin among unparked accounts — so a retry that ignored
    ``parked: false`` would reach the NEXT account and show."""

    def pick(self, provider, lane="cli"):
        answer = super().pick(provider, lane)
        self.order = self.order[1:] + self.order[:1]
        return answer


async def test_a_limit_iron_proxy_does_not_park_is_its_own_error_and_never_moves(tmp_path, monkeypatch, upstream):
    """Iron-Proxy was told and declined to park: no account switch, and the
    attempt's own error leaves MARKED — a raw 429 reads transient, so the router
    would retry the same account (Retry-After) and then fail over elsewhere."""
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "limited"), {"retry-after": "60"})})
    fake = Claude(tmp_path, monkeypatch)
    client = RotatingIronProxy("anthropic", homes, park=lambda *a: None)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert info.value.status_code == 429 and info.value.no_failover is True
    assert not is_transient_error(info.value)
    assert [s["status"] for s in client.signals] == [429] and up.hits == [homes["A"]]


class StickyIronProxy(FakeIronProxy):
    """Says it parked the account, then picks it again (an Iron-Proxy bug)."""

    def pick(self, provider, lane="cli"):
        self.parked.clear()
        return super().pick(provider, lane)


async def test_an_account_picked_again_after_a_park_is_not_retried(tmp_path, monkeypatch, upstream):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "limited"), {})})
    fake = Claude(tmp_path, monkeypatch)
    client = StickyIronProxy("anthropic", homes)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert info.value.status_code == 429 and info.value.no_failover is True
    assert up.hits == [homes["A"]], "each account at most once per call"


async def test_at_most_five_accounts_are_tried_for_one_call(tmp_path, monkeypatch, upstream):
    names = ["A", "B", "C", "D", "E", "F"]
    homes = _homes(tmp_path, *names)
    body = _err_body("rate_limit_error", "limited")
    up = upstream({h: (429, body, {}) for h in homes.values()})
    fake = Claude(tmp_path, monkeypatch)
    client = FakeIronProxy("anthropic", homes)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert accounts.MAX_ATTEMPTS == 5
    assert up.hits == [homes[n] for n in names[:5]]
    assert info.value.status_code == 429, "the cap raises the last attempt's own error"
    assert info.value.no_failover is True and not is_transient_error(info.value)


# --------------------------------------------------------------------------- #
# 6. Iron-Proxy failing never fails the turn
# --------------------------------------------------------------------------- #
async def test_iron_proxy_errors_are_swallowed(tmp_path, monkeypatch, upstream, caplog):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "limited"), {"retry-after": "3"})})
    fake = Claude(tmp_path, monkeypatch)
    client = FakeIronProxy("anthropic", homes)
    client.fail["signal"] = IronProxyError("UNREACHABLE", "connection refused")
    client.fail["finished"] = IronProxyError("UNREACHABLE", "connection refused")
    _use(client)
    caplog.set_level("INFO", logger="ironjarvis.iron_proxy")
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert str(info.value) == ('claude-cli: The Claude account "Title A" in Iron-Proxy hit a limit '
                               "and Iron-Proxy could not be told — try again shortly.")
    assert info.value.no_failover is True and not is_transient_error(info.value)
    assert info.value.retry_after == 3.0
    assert up.hits == [homes["A"]]
    # A success whose report fails is still a success.
    resp = await fake.adapter(upstream({}).url).complete(system="", messages=[_user()], tools=[])
    assert resp.text == "FIRST" and [f["id"] for f in client.done] == ["A"]
    lines = [r.getMessage() for r in caplog.records if r.name == "ironjarvis.iron_proxy"]
    assert len([x for x in lines if "signal failed" in x]) == 1
    assert len([x for x in lines if "finished failed" in x]) == 1
    assert "connection refused" not in caplog.text


# --------------------------------------------------------------------------- #
# 7. the relay exposes ONLY the allow-listed headers of the answered attempt
# --------------------------------------------------------------------------- #
def test_exposed_headers_is_an_allow_list():
    got = adm_mod.exposed_headers([
        ("Retry-After", "5"), ("anthropic-ratelimit-unified-reset", "x"), ("Authorization", "Bearer s"),
        ("set-cookie", "a=b"), ("x-api-key", "k"), ("request-id", "r"),
    ])
    assert got == {"retry-after": "5", "anthropic-ratelimit-unified-reset": "x"}


def test_the_limit_report_reads_only_limit_shapes():
    A = transport.Attempt
    assert A(relay_used=True, status=429, headers={"retry-after": "1"}, error_text="t").limit_report() == {
        "status": 429, "headers": {"retry-after": "1"}, "text": "t"}
    assert A(relay_used=True, status=401).limit_report()["status"] == 401
    for status in (503, 529):  # an overload is the provider's, not one account's
        assert A(relay_used=True, status=status).limit_report() is None
    assert A(relay_used=True, status=403, auth_refusal=True).limit_report()["status"] == 403
    assert A(relay_used=True, status=403).limit_report() is None  # model access
    assert A(relay_used=True, status=400).limit_report() is None
    assert A(relay_used=True, status=None).limit_report() is None  # a dropped connection
    assert A(native_error="Claude AI usage limit reached").limit_report() == {
        "text": "Claude AI usage limit reached"}
    assert A().limit_report() is None


def test_a_lease_never_applies_anything_but_the_home(monkeypatch):
    client = FakeIronProxy("anthropic", {"A": "C:/h/A"})
    client.extra_set = {"ANTHROPIC_BASE_URL": "https://evil.example", "PATH": "x"}
    client.unset = ["ANTHROPIC_API_KEY", "Path", "SystemRoot", "CLAUDE_CONFIG_DIR"]
    _use(client)
    lease = accounts.lease("claude-cli")
    assert lease.env_set == {"CLAUDE_CONFIG_DIR": "C:/h/A"}
    assert lease.env_unset == ("ANTHROPIC_API_KEY",)
    assert lease.title == "Title A" and lease.provider == "anthropic" and lease.home == "C:/h/A"
    assert accounts.lease("some-other-provider") is None


# --------------------------------------------------------------------------- #
# 8. codex: env + one retry
# --------------------------------------------------------------------------- #
FAKE_CODEX = r'''
import json, os, sys
home = os.environ.get("CODEX_HOME", "")
with open(os.environ["FAKE_CODEX_LOG"], "a", encoding="utf-8") as f:
    f.write(json.dumps({"home": home, "has_key": "OPENAI_API_KEY" in os.environ}) + "\n")
sys.stdin.read()
if home.endswith("home-A"):
    sys.stderr.write(os.environ.get("FAKE_CODEX_A_SAYS", "") + "\n")
    sys.exit(1)
argv = sys.argv[1:]
out = argv[argv.index("--output-last-message") + 1]
with open(out, "w", encoding="utf-8") as f:
    f.write("answer from " + (os.path.basename(home) or "default"))
'''


def _codex(tmp_path: Path, monkeypatch) -> tuple[sc.SubprocessCliAdapter, Path]:
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE_CODEX, encoding="utf-8")
    log = tmp_path / "codex.jsonl"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    shim = _shim(tmp_path, script, "codex")
    return sc.make_codex_cli(which=lambda _b: shim), log


def _codex_log(log: Path) -> list[dict]:
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("says", [
    "ERROR: You've hit your usage limit. Try again at 3:05 PM.",
    "ERROR: Not logged in. Please run `codex login`.",
])
async def test_codex_runs_as_the_account_and_retries_once_on_its_usage_limit(tmp_path, monkeypatch, _clean, says):
    monkeypatch.setenv("FAKE_CODEX_A_SAYS", says)
    adapter, log = _codex(tmp_path, monkeypatch)
    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("openai", homes)
    _use(client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-INHERITED")
    loop_thread = threading.get_ident()
    resp = await adapter.complete(system="", messages=[_user()], tools=[])
    assert resp.text == "answer from home-B"
    assert [r["home"] for r in _codex_log(log)] == [homes["A"], homes["B"]]
    assert not any(r["has_key"] for r in _codex_log(log)), "the account's unset list was applied"
    [sig] = client.signals
    assert sig["id"] == "A" and sig["status"] is None and sig["text"] == says
    assert client.parked["A"] in ("quota-exhausted", "auth-expired")
    assert [f["id"] for f in client.done] == ["B"]
    assert all(t != loop_thread for t in client.threads)
    assert _clean.status("codex") is None


async def test_codex_off_runs_with_the_inherited_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    adapter, log = _codex(tmp_path, monkeypatch)
    resp = await adapter.complete(system="", messages=[_user()], tools=[])
    assert resp.text == "answer from default"
    assert _codex_log(log) == [{"home": "", "has_key": False}]


async def test_codex_all_accounts_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CODEX_A_SAYS", "ERROR: You've hit your usage limit.")
    adapter, log = _codex(tmp_path, monkeypatch)
    homes = _homes(tmp_path, "A")
    client = StatefulIronProxy("openai", homes)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await adapter.complete(system="", messages=[_user()], tools=[])
    assert str(info.value).startswith(
        "codex-cli: The only ChatGPT (Codex) account in Iron-Proxy is at its usage limit; it is free again at ")
    assert [r["home"] for r in _codex_log(log)] == [homes["A"]]


# --------------------------------------------------------------------------- #
# 9. grok: the account's own home + one retry
# --------------------------------------------------------------------------- #
class _GrokResp:
    def __init__(self, status: int, text: str = "", payload=None, headers=None):
        self.status_code, self.text, self._payload = status, text, payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _GrokHTTP:
    def __init__(self, by_token: dict[str, _GrokResp]):
        self.by_token = by_token
        self.tokens: list[str] = []

    async def post(self, url, *, headers=None, json=None):
        token = headers["Authorization"].split(" ", 1)[1]
        self.tokens.append(token)
        return self.by_token[token]


def _grok_home(home: str, token: str | None) -> None:
    if token is not None:
        Path(home, "auth.json").write_text(
            json.dumps({"issuer::client": {"key": token, "expires_at": "2999-01-01T00:00:00Z"}}),
            encoding="utf-8")


def _grok_ok() -> _GrokResp:
    events = [{"type": "response.completed", "response": {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": "hi from grok"}]}],
        "usage": {"input_tokens": 4, "output_tokens": 2}}}]
    return _GrokResp(200, "".join(f"data: {json.dumps(e)}\n\n" for e in events))


async def test_grok_reads_each_accounts_home_and_retries_once(tmp_path):
    homes = _homes(tmp_path, "A", "B")
    _grok_home(homes["A"], "tokA")
    _grok_home(homes["B"], "tokB")
    http = _GrokHTTP({
        "tokA": _GrokResp(429, payload={"error": {"message": "rate limited"}},
                          headers={"retry-after": "30", "set-cookie": "x"}),
        "tokB": _grok_ok(),
    })
    client = FakeIronProxy("xai", homes)
    _use(client)
    adapter = grok_cli.GrokCliAdapter(http=http, session_provider=lambda: pytest.fail("default session read"))
    resp = await adapter.complete(system="", messages=[_user()], tools=[])
    assert resp.text == "hi from grok"
    assert http.tokens == ["tokA", "tokB"], "each call used THAT account's own session"
    [sig] = client.signals
    assert sig == {"id": "A", "status": 429, "headers": {"retry-after": "30"}, "text": "rate limited"}
    assert [f["id"] for f in client.done] == ["B"]
    assert "GROK_HOME" not in os.environ, "the process environment is never touched"


async def test_grok_an_account_with_no_session_is_reported_for_sign_in(tmp_path):
    homes = _homes(tmp_path, "A", "B")
    _grok_home(homes["B"], "tokB")  # A holds no auth.json
    http = _GrokHTTP({"tokB": _grok_ok()})
    client = FakeIronProxy("xai", homes)
    _use(client)
    resp = await grok_cli.GrokCliAdapter(http=http).complete(system="", messages=[_user()], tools=[])
    assert resp.text == "hi from grok" and http.tokens == ["tokB"]
    assert client.parked == {"A": "auth-expired"}


async def test_grok_off_uses_the_default_session(tmp_path):
    http = _GrokHTTP({"tok-default": _grok_ok()})
    session = {"token": "tok-default", "expires_at": "2999-01-01T00:00:00Z", "version": "0.2.82"}
    resp = await grok_cli.GrokCliAdapter(http=http, session_provider=lambda: session).complete(
        system="", messages=[_user()], tools=[])
    assert resp.text == "hi from grok" and http.tokens == ["tok-default"]


def test_the_lease_lookup_is_never_on_the_loop_source_pin():
    """Every adapter reaches Iron-Proxy through ``asyncio.to_thread`` (or the
    accounts helpers that hop themselves) — never a bare blocking call."""
    import inspect

    for fn in (sc.ClaudeCliAdapter._frames, sc.SubprocessCliAdapter.complete,
               grok_cli.GrokCliAdapter.complete, grok_cli.GrokCliAdapter.stream):
        src = inspect.getsource(fn)
        assert "asyncio.to_thread(accounts.lease" in src, fn.__qualname__
        assert "accounts.lease(" not in src.replace("asyncio.to_thread(accounts.lease,", "")


# --------------------------------------------------------------------------- #
# 10. the relay reads a refused request's body before replying (Windows RST)
# --------------------------------------------------------------------------- #
def _post_big(url: str, suffix: str, size: int = 512 * 1024) -> int:
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path + suffix, b"x" * size, {"Content-Type": "application/json"})
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


@pytest.mark.parametrize("round_", range(3))
def test_a_refusal_never_resets_the_connection(upstream, round_):
    """A 404 (wrong route) and a 400 (a second request) are answered only after
    the request body was read: replying over unread bytes made Windows reset
    the connection (WinError 10053) before the client could read the answer."""
    up = upstream({})
    adm = adm_mod.Admission(up.url, 5.0)
    try:
        assert _post_big(adm.url, "/v1/nope") == 404
        assert _post_big(adm.url, "/v1/messages", size=10) == 200
        assert _post_big(adm.url, "/v1/messages", size=16 * 1024 * 1024) == 400
        assert adm.denied == 1
    finally:
        adm.close()


# --------------------------------------------------------------------------- #
# 11. an Iron-Proxy sentence never moves the call to another provider
# --------------------------------------------------------------------------- #
from iron_jarvis.core.events import EventBus  # noqa: E402
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse  # noqa: E402
from iron_jarvis.providers.router import ModelRouter  # noqa: E402


class _Parked(LLMAdapter):
    def __init__(self, provider, exc):
        self.provider, self.model, self.exc, self.calls = provider, "m", exc, 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        raise self.exc


class _Healthy(LLMAdapter):
    def __init__(self, provider):
        self.provider, self.model, self.calls = provider, "ok-model", 0

    async def complete(self, *, system, messages, tools, **kw):
        self.calls += 1
        return LLMResponse(text=f"answer from {self.provider}", tool_calls=[], usage={})


class _Mgr:
    def __init__(self, adapters):
        self.adapters = adapters

    def available(self, provider):
        return provider in self.adapters

    def has_available_api_provider(self):
        return True

    def get(self, provider, model=None):
        return self.adapters[provider]


def _sentence(transient: bool, no_failover: bool) -> ProviderError:
    err = ProviderError("claude-cli: All 2 Claude accounts in Iron-Proxy are rate-limited.",
                        transient=transient)  # words that WOULD read transient without the flag
    if no_failover:
        err.no_failover = True
    return err


@pytest.mark.parametrize("lane", ["complete", "stream"])
@pytest.mark.parametrize("explicit", [True, False])
@pytest.mark.parametrize("transient", [False, True])
async def test_an_iron_proxy_sentence_never_fails_over(lane, explicit, transient):
    """Neither the default-provider fallback (A) nor the sideways failover (B)
    may move a no_failover sentence elsewhere — in BOTH lanes."""
    exc = _sentence(transient, no_failover=True)
    parked = _Parked("claude-cli", exc)
    others = {p: _Healthy(p) for p in ("anthropic", "openai", "codex-cli", "gemini")}
    mgr = _Mgr({"claude-cli": parked, **others})
    router = ModelRouter(mgr, default_provider="claude-cli" if not explicit else "anthropic",
                         event_bus=EventBus())
    kw = {"provider": "claude-cli"} if explicit else {}
    with pytest.raises(ProviderError) as info:
        if lane == "complete":
            await router.complete(system="", messages=[_user()], tools=[], **kw)
        else:
            async for _ in router.stream(system="", messages=[_user()], tools=[], **kw):
                pass
    assert info.value is exc
    assert not is_transient_error(exc), "never retried as a transient blip either"
    assert parked.calls == 1
    assert all(a.calls == 0 for a in others.values()), "no other provider was asked"
    assert router.health.allow("claude-cli"), "no breaker strike for parked accounts"


@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_the_same_error_without_the_flag_still_fails_over(lane):
    """Control: failover for every OTHER error is unchanged."""
    parked = _Parked("claude-cli", _sentence(False, no_failover=False))
    healthy = _Healthy("anthropic")
    router = ModelRouter(_Mgr({"claude-cli": parked, "anthropic": healthy}),
                         default_provider="anthropic", event_bus=EventBus())
    if lane == "complete":
        res = await router.complete(provider="claude-cli", system="", messages=[_user()], tools=[])
        assert res.provider == "anthropic"
    else:
        frames = [f async for f in router.stream(provider="claude-cli", system="", messages=[_user()], tools=[])]
        assert any(f.get("type") == "final" for f in frames)
    assert healthy.calls == 1


async def test_every_account_sentence_carries_no_failover(tmp_path, monkeypatch):
    fake = Claude(tmp_path, monkeypatch, "text")
    cases = [
        IronProxyError("AUTH_REQUIRED", "Invalid token.", status=401),
        IronProxyError("AUTH_REQUIRED", "x", status=401, details={"profileId": "A", "title": "Work"}),
        IronProxyError("ALL_PROFILES_EXHAUSTED", "x", status=429, details={"tried": ["A"]}),
        IronProxyError("INVALID_REQUEST", "No control route GET /iron/pick.", status=404),
        IronProxyError("UNREACHABLE", "connection refused"),
    ]
    for err in cases:
        client = FakeIronProxy("anthropic", {})
        client.fail["pick"] = err
        _use(client)
        with pytest.raises(ProviderError) as info:
            await fake.adapter().complete(system="", messages=[_user()], tools=[])
        assert getattr(info.value, "no_failover", False) is True, err.code
        assert not info.value.transient


# --------------------------------------------------------------------------- #
# 12. Iron-Proxy ON is never a silent default login (item E)
# --------------------------------------------------------------------------- #
def _unavailable_cls():
    from iron_jarvis.iron_proxy import service as svc_mod

    cls = getattr(svc_mod, "IronProxyUnavailable", None)
    if cls is None:
        class IronProxyUnavailable(Exception):  # noqa: N818 — the service's name, until it lands
            def __init__(self, sentence):
                super().__init__(sentence)
                self.sentence = sentence
        cls = IronProxyUnavailable
    return cls


async def test_an_iron_proxy_that_is_on_but_unusable_says_so(tmp_path, monkeypatch):
    from iron_jarvis.iron_proxy import service as svc_mod

    fake = Claude(tmp_path, monkeypatch, "text")
    cls = _unavailable_cls()
    words = ("Iron-Proxy is still starting. Log the account in again: iron-proxy login abc, "
             "or 'Log in' on it in the switcher.")
    try:
        boom = cls(words)
    except TypeError:
        boom = cls.__new__(cls)
        Exception.__init__(boom, words)
    boom.sentence = words

    class _On:
        def __init__(self, result):
            self.result, self.timeouts = result, []

        def lease_client(self, timeout_s=None):
            self.timeouts.append(timeout_s)
            if isinstance(self.result, BaseException):
                raise self.result
            return self.result

        def client(self):  # the reporting path
            return None

    on = _On(boom)
    monkeypatch.setattr(svc_mod, "current", lambda: on)
    accounts.set_client_source(None)
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    said = str(info.value)
    assert said.startswith("claude-cli: Iron-Proxy is still starting.")
    assert "iron-proxy login" not in said and "switcher" not in said
    assert "Connections page" in said
    assert info.value.no_failover is True and not info.value.transient
    assert on.timeouts == [accounts.LEASE_CLIENT_TIMEOUT_S]
    assert not fake.record_path.exists(), "nothing ran on the default login"
    # Disabled (lease_client answers None) = exactly today's run.
    on.result = None
    resp = await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert resp.text == "Hello" and "CLAUDE_CONFIG_DIR" not in fake.record["env"]


async def test_an_iron_proxy_too_old_for_the_executor_api_says_so(tmp_path, monkeypatch):
    fake = Claude(tmp_path, monkeypatch, "text")
    client = FakeIronProxy("anthropic", {})
    client.fail["pick"] = IronProxyError("INVALID_REQUEST", "No control route GET /iron/pick.", status=404)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert str(info.value) == (
        "claude-cli: The Iron-Proxy running on this PC is too old to lend its Claude accounts to "
        "Iron Jarvis — update Iron-Proxy, or turn it off on the Connections page."
    )
    assert not fake.record_path.exists()


def test_iron_proxy_hints_point_at_the_connections_page():
    assert accounts.plain_hint(
        "Log \"Work\" in again: iron-proxy login p_1, or 'Log in' on it in the switcher."
    ) == 'Sign "Work" in again on the Connections page (Iron-Proxy card).'
    out = accounts.plain_hint(
        "Wait until 3:05 PM for the first reset, or add another anthropic account: "
        'iron-proxy profiles add --provider anthropic --lane cli --title "...".'
    )
    assert "iron-proxy" not in out and "the Connections page (Iron-Proxy card)" in out
    out = accounts.plain_hint(
        "Add an account for anthropic: iron-proxy profiles add --provider anthropic --lane cli "
        "--title \"...\" (then iron-proxy login <id>), or 'Add account' in the switcher."
    )
    assert out == "Add an account on the Connections page (Iron-Proxy card), then sign it in there."
    assert "switcher" not in accounts.plain_hint("Run iron-proxy profiles list (or open the switcher).")


# --------------------------------------------------------------------------- #
# 13. an overload is the provider's, not one account's (item B)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("status", [503, 529])
async def test_an_overload_is_never_reported_and_never_rotates(tmp_path, monkeypatch, upstream, status):
    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (status, _err_body("overloaded_error", "Overloaded"), {"retry-after": "4"})})
    fake = Claude(tmp_path, monkeypatch)
    client = FakeIronProxy("anthropic", homes, park=lambda *a: "overloaded")  # would park if told
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    # Exactly the v1.300.0 error: its status, transient, Retry-After, no no_failover.
    assert info.value.status_code == status and info.value.transient and info.value.retry_after == 4.0
    assert not getattr(info.value, "no_failover", False)
    assert client.signals == [] and up.hits == [homes["A"]] and client.picks == ["A"]


def test_overload_only_words_are_not_an_account_signal():
    assert accounts._not_an_account_signal({"status": 529})
    assert accounts._not_an_account_signal({"status": 503})
    assert not accounts._not_an_account_signal({"status": 429})
    assert accounts._not_an_account_signal({"text": "ERROR: 503 Service Unavailable"})
    assert accounts._not_an_account_signal({"text": "ERROR: stream error: error sending request"})
    assert accounts._not_an_account_signal({"status": 400})
    assert not accounts._not_an_account_signal({"status": 401})
    assert accounts._not_an_account_signal({"text": "   "})
    assert not accounts._not_an_account_signal({"text": "ERROR: You've hit your usage limit (503 later)"})


# --------------------------------------------------------------------------- #
# 14. all parked: said BY KIND (item B)
# --------------------------------------------------------------------------- #
class StatefulIronProxy(FakeIronProxy):
    """Adds Iron-Proxy's /iron/profiles + /iron/states over the same parks."""

    def __init__(self, *a, until: str = "", **kw):
        super().__init__(*a, **kw)
        self.until = until or (datetime.now(timezone.utc) + timedelta(minutes=50)).isoformat()

    def pick(self, provider, lane="cli"):
        free = [p for p in self.order if p not in self.parked]
        if not free:
            raise IronProxyError("ALL_PROFILES_EXHAUSTED", "Every account is parked.", status=429,
                                 details={"provider": provider, "resetAt": self.reset_at,
                                          "tried": list(self.order)})
        return super().pick(provider, lane)

    def profiles(self):
        return [{"id": p, "title": f"Title {p}", "provider": self.provider, "lane": "cli", "enabled": True}
                for p in self.order] + [{"id": "X", "title": "Other", "provider": "openai", "lane": "cli"}]

    def states(self):
        out = {}
        for p in self.order:
            kind = self.parked.get(p)
            if kind == "auth-expired":
                out[p] = {"profileId": p, "status": "unauthenticated", "parkedReason": {"kind": kind}}
            elif kind:
                out[p] = {"profileId": p, "status": "parked", "parkedUntil": self.until,
                          "parkedReason": {"kind": kind}}
            else:
                out[p] = {"profileId": p, "status": "ready"}
        return out


async def _all_parked(tmp_path, monkeypatch, kinds: dict[str, str]) -> ProviderError:
    fake = Claude(tmp_path, monkeypatch, "text")
    client = StatefulIronProxy("anthropic", {k: f"C:/h/{k}" for k in kinds})
    client.parked = dict(kinds)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert info.value.no_failover is True and not fake.record_path.exists()
    return info.value


async def test_all_rate_limited_says_rate_limited(tmp_path, monkeypatch):
    exc = await _all_parked(tmp_path, monkeypatch, {"A": "rate-limit", "B": "rate-limit"})
    assert str(exc).startswith("claude-cli: All 2 Claude accounts in Iron-Proxy are at their request "
                               "limit; the first is free again at ")
    assert not is_transient_error(exc)
    assert exc.retry_after is not None


async def test_all_at_the_usage_limit_says_usage_limit(tmp_path, monkeypatch):
    exc = await _all_parked(tmp_path, monkeypatch, {"A": "quota-exhausted", "B": "billing"})
    assert "are at their usage limit; the first is free again at" in str(exc)


async def test_all_signed_out_says_sign_in_never_limit(tmp_path, monkeypatch):
    exc = await _all_parked(tmp_path, monkeypatch, {"A": "auth-expired", "B": "auth-expired"})
    assert str(exc) == ("claude-cli: Both Claude accounts in Iron-Proxy need to sign in again — sign "
                        "them in on the Connections page (Iron-Proxy card), then try again.")
    assert exc.retry_after is None


async def test_a_mix_names_each_account(tmp_path, monkeypatch):
    exc = await _all_parked(tmp_path, monkeypatch, {"A": "rate-limit", "B": "auth-expired"})
    said = str(exc)
    assert "at their" not in said, "a mix is never 'all at their limit'"
    assert said.startswith("claude-cli: No Claude account in Iron-Proxy can take this right now: ")
    assert '"Title A" is at its request limit until ' in said
    assert '"Title B" needs to sign in again on the Connections page (Iron-Proxy card)' in said
    assert "Other" not in said, "another provider's account is never named"


async def test_kind_unknown_is_a_generic_sentence(tmp_path, monkeypatch):
    fake = Claude(tmp_path, monkeypatch, "text")
    client = FakeIronProxy("anthropic", {"A": "C:/h/A", "B": "C:/h/B"})
    client.parked = {"A": "rate-limit", "B": "rate-limit"}
    _use(client)  # no profiles()/states(): Iron-Proxy cannot say which kind
    with pytest.raises(ProviderError) as info:
        await fake.adapter().complete(system="", messages=[_user()], tools=[])
    assert str(info.value).startswith(
        "claude-cli: Every Claude account in Iron-Proxy is parked right now (a limit or a sign-in)")


# --------------------------------------------------------------------------- #
# 15. codex: only codex's OWN error lines reach Iron-Proxy (item A)
# --------------------------------------------------------------------------- #
#: A faithful copy of Iron-Proxy's detectFromCliOutput (packages/core/src/quota/detect.ts).
_IP_AUTHY = re.compile(r"not (?:logged in|authenticated)|please (?:log ?in|sign in|run .*login)|invalid "
                       r"(?:api key|token)|token (?:has )?expired|authentication (?:failed|required)"
                       r"|unauthorized|re-?authenticat", re.I)
_IP_LIMIT = re.compile(r"(?:usage|rate|weekly|daily|monthly|session|5-hour|five-hour) limit|limit "
                       r"(?:reached|exceeded|hit)|you(?:'ve| have) hit your|out of (?:credits|quota)"
                       r"|quota (?:exceeded|exhausted)|too many requests|resource_exhausted|429", re.I)
_IP_OVERLOAD = re.compile(r"overloaded|capacity|529|503|temporarily unavailable|service unavailable", re.I)
_IP_BILLING = re.compile(r"credit balance|insufficient[_ ]quota|billing|payment required|upgrade your plan", re.I)


def iron_proxy_cli_kind(status, headers, text):
    t = (text or "").lower()
    if not t.strip():
        return None
    if _IP_BILLING.search(t):
        return "billing"
    if _IP_AUTHY.search(t):
        return "auth-expired"
    if _IP_LIMIT.search(t):
        return "quota-exhausted"
    if _IP_OVERLOAD.search(t):
        return "overloaded"
    return None


PROMPT_WITH_MONEY_WORDS = "Reconcile the client's credit balance and the billing memo for March."
CODEX_STDERR = (
    "OpenAI Codex v0.46.0 (research preview)\n--------\nworkdir: C:\\x\nmodel: gpt-5\n--------\n"
    "user\n" + PROMPT_WITH_MONEY_WORDS + "\n"
    "ERROR: stream error: error sending request for url (https://chatgpt.com/backend-api/codex/responses); retry 5/5\n"
)

FAKE_CODEX_STDERR = r'''
import os, sys
sys.stdin.read()
sys.stderr.write(os.environ["FAKE_CODEX_STDERR"])
sys.exit(1)
'''


async def test_codex_never_hands_the_users_prompt_to_iron_proxy(tmp_path, monkeypatch, _clean):
    script = tmp_path / "fake_codex_stderr.py"
    script.write_text(FAKE_CODEX_STDERR, encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX_STDERR", CODEX_STDERR)
    shim = _shim(tmp_path, script, "codex")
    adapter = sc.make_codex_cli(which=lambda _b: shim)
    homes = _homes(tmp_path, "A", "B")
    client = FakeIronProxy("openai", homes, park=iron_proxy_cli_kind)
    _use(client)
    assert iron_proxy_cli_kind(None, None, CODEX_STDERR) == "billing", "the whole stderr WOULD park"
    with pytest.raises(RuntimeError) as info:
        await adapter.complete(system="", messages=[_user(PROMPT_WITH_MONEY_WORDS)], tools=[])
    sent = sc.cli_error_lines(CODEX_STDERR, "", sc._flatten("", [_user(PROMPT_WITH_MONEY_WORDS)]))
    assert sent.startswith("ERROR: stream error") and "billing" not in sent and PROMPT_WITH_MONEY_WORDS not in sent
    # A network error is no account's limit: nothing reported, nothing parked,
    # no rotation, and the error keeps its failover (not marked).
    assert client.signals == [] and client.parked == {} and client.picks == ["A"]
    assert not getattr(info.value, "no_failover", False)


def test_cli_error_lines_keeps_only_the_clis_words():
    err = "banner\nuser\nmy prompt says ERROR: credit balance\nERROR: usage limit reached\ntail line\n"
    got = sc.cli_error_lines(err, "", "my prompt says ERROR: credit balance")
    assert got.splitlines() == ["ERROR: usage limit reached"], "ONLY the CLI's ERROR lines"
    assert sc.cli_error_lines("", "", "p") == ""
    # An ERROR-shaped line the USER wrote (pasted into the prompt, echoed back
    # by codex) is never handed over as codex's own words.
    nl = chr(10)
    prompt = "Why does it say this?" + nl + "ERROR: credit balance below zero on the billing account"
    err = "user" + nl + prompt + nl + "ERROR: stream disconnected before completion" + nl
    assert sc.cli_error_lines(err, "", prompt) == "ERROR: stream disconnected before completion"
    assert sc.cli_error_lines("a\nmy question", "", "my question") == ""


async def test_codex_overload_words_are_never_reported(tmp_path, monkeypatch):
    """An overload in the CLI's own words is the provider's: not reported, no
    rotation, raised as before — even to an Iron-Proxy that would park it."""
    script = tmp_path / "fake_codex_stderr.py"
    script.write_text(FAKE_CODEX_STDERR, encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX_STDERR", "ERROR: unexpected status 503 Service Unavailable" + chr(10))
    shim = _shim(tmp_path, script, "codex")
    adapter = sc.make_codex_cli(which=lambda _b: shim)
    client = FakeIronProxy("openai", _homes(tmp_path, "A", "B"), park=lambda *a: "overloaded")
    _use(client)
    with pytest.raises(RuntimeError) as info:
        await adapter.complete(system="", messages=[_user()], tools=[])
    assert "503" in str(info.value)
    assert client.signals == [] and client.picks == ["A"]


async def test_grok_overload_is_never_reported(tmp_path):
    homes = _homes(tmp_path, "A", "B")
    _grok_home(homes["A"], "tokA")
    _grok_home(homes["B"], "tokB")
    http = _GrokHTTP({"tokA": _GrokResp(529, payload={"error": {"message": "overloaded"}}), "tokB": _grok_ok()})
    client = FakeIronProxy("xai", homes, park=lambda *a: "overloaded")
    _use(client)
    with pytest.raises(ProviderError) as info:
        await grok_cli.GrokCliAdapter(http=http).complete(system="", messages=[_user()], tools=[])
    assert info.value.status_code == 529 and info.value.transient
    assert client.signals == [] and http.tokens == ["tokA"]



async def test_codex_the_models_own_last_line_is_never_reported(tmp_path, monkeypatch):
    """A non-zero exit with no ERROR line reports NOTHING — the last line can be
    the model's own answer, and it may speak of billing (reviewer's case)."""
    script = tmp_path / "fake_codex_stderr.py"
    script.write_text(FAKE_CODEX_STDERR, encoding="utf-8")
    monkeypatch.setenv("FAKE_CODEX_STDERR", "codex\nThe March billing statement shows a credit balance.\n")
    shim = _shim(tmp_path, script, "codex")
    adapter = sc.make_codex_cli(which=lambda _b: shim)
    client = FakeIronProxy("openai", _homes(tmp_path, "A", "B"), park=iron_proxy_cli_kind)
    _use(client)
    with pytest.raises(RuntimeError):
        await adapter.complete(system="", messages=[_user("summarise March")], tools=[])
    assert client.signals == [] and client.parked == {} and client.picks == ["A"]


# --------------------------------------------------------------------------- #
# 16. once a lease existed, an ACCOUNT failure never leaves unmarked (review 2)
# --------------------------------------------------------------------------- #
async def test_an_untold_429_never_fails_over_through_the_router(tmp_path, monkeypatch, upstream):
    """The reviewer's reproduction: A answers 429 with Retry-After 60 and
    Iron-Proxy cannot be told. The router must neither retry A (151 s of
    Retry-After) nor move the turn to codex-cli."""
    import time as _time

    homes = _homes(tmp_path, "A", "B")
    up = upstream({homes["A"]: (429, _err_body("rate_limit_error", "limited"), {"retry-after": "60"})})
    fake = Claude(tmp_path, monkeypatch)
    client = FakeIronProxy("anthropic", homes)
    client.fail["signal"] = IronProxyError("UNREACHABLE", "connection refused")
    _use(client)
    codex = _Healthy("codex-cli")
    router = ModelRouter(_Mgr({"claude-cli": fake.adapter(up.url), "codex-cli": codex,
                               "anthropic": _Healthy("anthropic")}),
                         default_provider="anthropic", event_bus=EventBus())
    t0 = _time.monotonic()
    for lane in ("complete", "stream"):
        with pytest.raises(ProviderError) as info:
            if lane == "complete":
                await router.complete(provider="claude-cli", system="", messages=[_user()], tools=[])
            else:
                async for _ in router.stream(provider="claude-cli", system="", messages=[_user()], tools=[]):
                    pass
        assert "could not be told" in str(info.value) and info.value.no_failover is True
    assert _time.monotonic() - t0 < 60, "never waited out a Retry-After on the same account"
    assert codex.calls == 0 and up.hits == [homes["A"], homes["A"]], "one attempt per turn, no failover"


# --------------------------------------------------------------------------- #
# 17. AUTO is the user's one opted-in exception (review 2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_an_auto_turn_may_substitute_and_says_so(lane):
    exc = _sentence(False, no_failover=True)
    parked = _Parked("claude-cli", exc)
    healthy = _Healthy("anthropic")

    async def auto_route(system, messages, tools, task_class):
        return {"provider": "claude-cli", "model": None, "tier": "t"}

    router = ModelRouter(_Mgr({"claude-cli": parked, "anthropic": healthy}),
                         default_provider="auto", event_bus=EventBus(), auto_route=auto_route)
    for _ in range(3):  # the breaker opens after 3 strikes: parked accounts strike nothing
        if lane == "complete":
            res = await router.complete(system="", messages=[_user()], tools=[])
            assert res.provider == "anthropic" and res.reason == "failover"
            assert res.from_provider == "claude-cli"
        else:
            frames = [f async for f in router.stream(system="", messages=[_user()], tools=[])]
            final = [f for f in frames if f.get("type") == "final"][-1]
            assert final.get("provider") == "anthropic" and final.get("reason") == "failover"
            assert final.get("from") == "claude-cli"
    assert parked.calls == 3 and healthy.calls == 3
    assert router.health.allow("claude-cli"), "no breaker strike for parked accounts, even on Auto"


# --------------------------------------------------------------------------- #
# 18. every Iron-Proxy hint that names its CLI or switcher becomes a whole
#     sentence about the Connections page — pinned against the REAL hints
# --------------------------------------------------------------------------- #
VENDORED_BUNDLE = Path(__file__).resolve().parents[1] / "desktop" / "vendor" / "iron-proxy" / "iron-proxy.mjs"

#: Iron-Proxy's DEFAULT_HINTS (packages/core/src/errors.ts) that mention its CLI
#: or the tray switcher -> exactly what the user reads in Iron Jarvis.
EXPECTED_HINTS = {
    "NO_PROFILE": "Add an account on the Connections page (Iron-Proxy card), then sign it in there.",
    "PROFILE_NOT_FOUND": "Check the account list on the Connections page (Iron-Proxy card).",
    "AUTH_REQUIRED": "Sign the account in again on the Connections page (Iron-Proxy card).",
    "ALL_PROFILES_EXHAUSTED": ("Wait for the earliest reset, or add another account of this provider on the "
                               "Connections page (Iron-Proxy card)."),
    "PROVIDER_ERROR": ("Retry in a moment; if it keeps failing, check the provider's status page and the "
                       "Iron-Proxy card on the Connections page."),
    "CLI_NOT_FOUND": ("Install the vendor CLI and put it on PATH, then check the Iron-Proxy card on the "
                      "Connections page."),
    "CLI_FAILED": "Sign the account in again on the Connections page (Iron-Proxy card).",
    "INVALID_REQUEST": "Update Iron-Proxy, or turn it off and on again on the Connections page (Iron-Proxy card).",
}

#: The per-error hints errors.ts BUILDS (provider / account / reset filled in).
DYNAMIC_HINTS = [
    ('Add an account for anthropic: iron-proxy profiles add --provider anthropic --lane cli --title "..." '
     "(then iron-proxy login <id>), or 'Add account' in the switcher.",
     "Add an account on the Connections page (Iron-Proxy card), then sign it in there."),
    ("Log \"Work\" in again: iron-proxy login p_1, or 'Log in' on it in the switcher.",
     'Sign "Work" in again on the Connections page (Iron-Proxy card).'),
    ("Log account p_1 in again: iron-proxy login p_1, or 'Log in' on it in the switcher.",
     "Sign account p_1 in again on the Connections page (Iron-Proxy card)."),
    ("Enter the API key for \"Work\" again: 'Set API key' on it in the switcher.",
     'Enter the API key for "Work" again in the Iron-Proxy app.'),
    ('Wait until 10/3/2026, 3:05:00 PM for the first reset, or add another anthropic account: '
     'iron-proxy profiles add --provider anthropic --lane cli --title "...".',
     "Wait until 10/3/2026, 3:05:00 PM for the first reset, or add another anthropic account on the "
     "Connections page (Iron-Proxy card)."),
    ('Wait for the first account to reset, or add another openai account: '
     'iron-proxy profiles add --provider openai --lane cli --title "...".',
     "Wait for the first account to reset, or add another openai account on the Connections page "
     "(Iron-Proxy card)."),
    ('Install "foo" and put it on PATH, then run iron-proxy doctor to confirm.',
     'Install "foo" and put it on PATH, then check the Iron-Proxy card on the Connections page.'),
]


def _vendored_default_hints() -> dict[str, str]:
    if not VENDORED_BUNDLE.exists():
        pytest.skip("the vendored Iron-Proxy bundle is not in this checkout")
    src = VENDORED_BUNDLE.read_text(encoding="utf-8").replace("\r\n", "\n")
    block = src[src.index("var DEFAULT_HINTS = {"):]
    block = block[:block.index("};")]
    hints = {m.group(1): m.group(3)
             for m in re.finditer(r'^\s+([A-Z_]+): (["`])(.*)\2,?$', block, re.M)}
    assert len(hints) >= 15, hints
    return hints


def _reads_as_a_sentence(text: str) -> None:
    assert text[:1].isupper() and text.endswith("."), text
    assert "iron-proxy " not in text and "switcher" not in text, text
    assert not re.search(r"\b(?:Run|Open|Check) the Connections page\b", text), text


def test_every_real_default_hint_is_a_whole_sentence():
    hints = _vendored_default_hints()
    names_cli = {code for code, text in hints.items() if re.search(r"iron-proxy [a-z-]|switcher", text)}
    assert names_cli == set(EXPECTED_HINTS), "a new Iron-Proxy hint names its CLI: word it here"
    for code, text in hints.items():
        out = accounts.plain_hint(text)
        if code in EXPECTED_HINTS:
            assert out == EXPECTED_HINTS[code], code
            _reads_as_a_sentence(out)
        else:
            assert out == text, f"{code}: a hint that names no CLI is left as Iron-Proxy wrote it"


@pytest.mark.parametrize("hint,expected", DYNAMIC_HINTS)
def test_every_built_hint_is_a_whole_sentence(hint, expected):
    out = accounts.plain_hint(hint)
    assert out == expected
    _reads_as_a_sentence(out)


def test_an_unknown_cli_mention_becomes_one_generic_sentence():
    out = accounts.plain_hint("Iron-Proxy is not answering. Run iron-proxy frobnicate now. Then iron-proxy x.")
    assert out == "Iron-Proxy is not answering. See the Iron-Proxy card on the Connections page."


async def test_the_cap_ends_with_the_by_kind_sentence_when_every_account_is_parked(
        tmp_path, monkeypatch, upstream):
    """Five accounts, five 429s: the cap ends the call, and one more pick turns
    it into the by-kind sentence instead of the fifth raw 429."""
    names = ["A", "B", "C", "D", "E"]
    homes = _homes(tmp_path, *names)
    body = _err_body("rate_limit_error", "limited")
    up = upstream({h: (429, body, {}) for h in homes.values()})
    fake = Claude(tmp_path, monkeypatch)
    client = StatefulIronProxy("anthropic", homes)
    _use(client)
    with pytest.raises(ProviderError) as info:
        await fake.adapter(up.url).complete(system="", messages=[_user()], tools=[])
    assert str(info.value).startswith("claude-cli: All 5 Claude accounts in Iron-Proxy are at their "
                                      "request limit; the first is free again at ")
    assert info.value.no_failover is True and up.hits == [homes[n] for n in names]
