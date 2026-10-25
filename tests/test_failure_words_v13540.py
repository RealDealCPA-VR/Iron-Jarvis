"""v1.354.0 (G28) — failures from the daemon come in plain words with a headline.

THE GAP (product-readiness audit, sections G/I/M): a signed-out Claude
refusal read "anthropic runs through claude-cli, which is installed but not
signed in … Open a terminal, run `claude`, then `/login`"; a local endpoint's
answered error named the config key ``local_primary_policy``; an API-key
provider's failure was the adapter's "openrouter API error 401: No auth
credentials found"; a run with no summary read "Session failed:
ProviderError: …"; and every chat error was ONE string with no headline.

ACCEPTANCE: a signed-out Claude refusal says "Claude Code isn't signed in"
with no raw ids or config keys; an OpenRouter 401 arrives with title
"OpenRouter refused your key" and the server text only in ``technical``.

Every pin drives the REAL code: the real router (with adapter doubles that
raise what the real adapters raise), the real app factory's two chat lanes,
and the real orchestrator finalizer.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentType, Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ProviderError
from iron_jarvis.providers.failure_words import (
    ACCOUNTS_PLACE,
    GENERIC_TITLE,
    friendly_error,
    heading,
    plain_name,
)
from iron_jarvis.providers.manager import ProviderManager
from tests.test_local_primary_policy_v1228 import _Bus, _Fail, _Manager, _msgs, _router

OPENROUTER_401 = "openrouter API error 401: No auth credentials found"


def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        event, data = None, None
        for line in block.strip().splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = json.loads(line[len("data:"):].strip())
        if event is not None:
            out.append((event, data if isinstance(data, dict) else {}))
    return out


# --------------------------------------------------------------------------- #
# The signed-out refusal (the router's own words).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("default", ["claude-cli", "anthropic"])
async def test_a_signed_out_claude_says_claude_code_isnt_signed_in_in_plain_words(
    tmp_path, monkeypatch, default
):
    monkeypatch.setattr(ProviderManager, "_cli_binary_present", staticmethod(lambda b: b == "claude"))
    monkeypatch.setattr(ProviderManager, "_cli_signed_in", staticmethod(lambda b: False))
    platform = build_platform(str(tmp_path))
    platform.config.default_provider = default
    with pytest.raises(ProviderError) as ei:
        await platform.router.complete(system="", messages=_msgs(), tools=[])
    text = str(ei.value)
    assert text.startswith("Claude Code isn't signed in, so this turn was not answered."), text
    # No raw ids, no config keys, no terminal commands.
    for raw in ("claude-cli", "anthropic runs", "local_primary_policy", "/login", "`claude`", "terminal"):
        assert raw not in text, (raw, text)
    assert f"Press Sign in beside Claude Code in {ACCOUNTS_PLACE}, then retry." in text
    if default == "anthropic":
        assert "Anthropic answers through your Claude Code sign-in." in text
    # The typed fields ride the refusal, and the chat's headline reads them.
    assert ei.value.kind == "signed_out" and ei.value.cli == "claude-cli"
    words = friendly_error(ei.value)
    assert words["title"] == "Claude Code isn't signed in"
    assert words["fix"] == f"Press Sign in beside Claude Code in {ACCOUNTS_PLACE}, then retry."
    assert words["technical"] == text


# --------------------------------------------------------------------------- #
# The answered-error refusal no longer names the config key.
# --------------------------------------------------------------------------- #
async def test_a_local_answered_error_says_the_policy_in_plain_words_and_the_banner_too():
    bus = _Bus()
    local = _Fail("ollama", ProviderError("ollama API error 500: upstream died", status_code=500))
    router = _router(_Manager({"ollama": local}), "ollama", bus, deadline_s=1.0)
    with pytest.raises(ProviderError) as ei:
        await router.complete(system="", messages=_msgs(), tools=[])
    text = str(ei.value)
    assert "Iron Jarvis won't switch to a different AI without asking. You can allow that in Settings." in text
    assert "local_primary_policy" not in text and "failover" not in text
    banner = bus.of(EventType.PROVIDER_DOWNGRADED)[-1]
    assert "local_primary_policy" not in banner["reason"]
    assert "answered with an error" in banner["reason"]
    words = friendly_error(ei.value)
    assert words["title"] == "Ollama answered with an error"
    assert "won't switch to a different AI without asking" in words["fix"]


# --------------------------------------------------------------------------- #
# The router stamps WHO failed, so the headline names the right provider.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lane", ["complete", "stream"])
async def test_the_router_stamps_the_failed_provider_and_the_headline_names_it(lane):
    # The Anthropic SDK's message carries no "<provider> API error" prefix:
    # only the router's stamp can name who refused the key.
    exc = ProviderError("Error code: 401 - invalid x-api-key", status_code=401)
    router = _router(_Manager({"anthropic": _Fail("anthropic", exc)}), "anthropic", deadline_s=1.0)
    with pytest.raises(ProviderError) as ei:
        if lane == "complete":
            await router.complete(system="", messages=_msgs(), tools=[])
        else:
            async for _ in router.stream(system="", messages=_msgs(), tools=[]):
                pass
    assert ei.value.failed_provider == "anthropic"
    words = friendly_error(ei.value)
    assert words["title"] == "Anthropic refused your key"
    assert words["fix"] == f"Update it in {ACCOUNTS_PLACE}."
    assert "x-api-key" not in words["title"] + words["fix"]
    assert "invalid x-api-key" in words["technical"]


# --------------------------------------------------------------------------- #
# The words themselves (deterministic, no model call).
# --------------------------------------------------------------------------- #
def test_the_words_by_status_and_by_transport():
    assert friendly_error(ProviderError(OPENROUTER_401, status_code=401))["title"] == "OpenRouter refused your key"
    assert friendly_error(ProviderError("xai API error 429: slow", status_code=429))["title"] == "xAI is busy"
    assert friendly_error(ProviderError("google API error 503: x", status_code=503))["title"] == "Google Gemini is busy"
    # The router's "every connected model is busy" wraps the real failure.
    try:
        try:
            raise ProviderError("openrouter API error 529: overloaded", status_code=529)
        except ProviderError as inner:
            raise RuntimeError("every connected model is rate-limited or unavailable") from inner
    except RuntimeError as outer:
        assert friendly_error(outer)["title"] == "OpenRouter is busy"
    # A subscription CLI refused: its sign-in, never "your key".
    cli = ProviderError("claude-cli: auth refused", status_code=401)
    cli.failed_provider = "claude-cli"
    assert friendly_error(cli)["title"] == "Claude Code refused your sign-in"
    # Anything the words cannot name: the generic headline, the detail kept.
    w = friendly_error(RuntimeError("boom"))
    assert w["title"] == GENERIC_TITLE and w["technical"] == "boom"


def test_a_secret_in_the_server_text_never_reaches_any_field():
    key = "sk-or-v1-abcdef0123456789abcdef"
    w = friendly_error(ProviderError(f"openrouter API error 401: Incorrect API key provided: {key}", status_code=401))
    assert key not in json.dumps(w)
    assert "sk-***" in w["technical"]


def test_names_keep_a_brands_casing_and_a_fleet_label():
    assert plain_name("claude-cli") == "Claude Code" and plain_name("codex-cli") == "Codex"
    assert heading(plain_name("xai")) == "xAI", "a brand keeps its casing (never 'XAI')"
    assert heading(plain_name("custom")) == "Your own endpoint"
    assert plain_name("openai (ChatGPT backend)") == "OpenAI"


# --------------------------------------------------------------------------- #
# Both chat lanes carry the headline beside the unchanged detail (lock-step).
# --------------------------------------------------------------------------- #
def _failing_router(platform, monkeypatch, exc):
    async def fake_complete(**kw):
        raise exc

    async def fake_stream(**kw):
        raise exc
        yield  # pragma: no cover

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)


def test_the_stream_lane_error_frame_has_title_fix_and_technical(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, ProviderError(OPENROUTER_401, status_code=401))
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200, r.text
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["detail"] == OPENROUTER_401, "detail is unchanged"
    assert err["title"] == "OpenRouter refused your key"
    assert err["fix"] == f"Update it in {ACCOUNTS_PLACE}."
    assert err["technical"] == OPENROUTER_401
    assert "No auth credentials" not in err["title"] + err["fix"], "the server text only in technical"


def test_the_post_lane_502_has_the_same_keys(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, ProviderError(OPENROUTER_401, status_code=401))
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502, r.text
    body = r.json()
    assert body == {
        "detail": OPENROUTER_401,
        "title": "OpenRouter refused your key",
        "fix": f"Update it in {ACCOUNTS_PLACE}.",
        "technical": OPENROUTER_401,
        # v1.355.0 (G29, on purpose): the one press that fixes it — the
        # chat's failure line puts "Update the key in Connections" beside
        # the headline from these two typed keys.
        "fix_kind": "key",
        "fix_provider": "openrouter",
    }


def test_the_clis_own_sign_in_refusal_mid_turn_reads_as_signed_out(tmp_path, monkeypatch):
    """v1.354.0 review: when the probe had not said so yet, the Claude CLI
    refuses MID-TURN and the transport raises a plain RuntimeError carrying
    cli_auth's remedy sentence (no typed kind, no status). It used to get the
    generic headline and "Press Retry", which can never help. Built by the
    REAL transport helper, through the REAL stream lane."""
    from iron_jarvis.providers.adapters.claude_native.transport import signed_out_message

    exc = RuntimeError(signed_out_message("Not logged in · Please run /login", probe=False))
    client = TestClient(create_app(str(tmp_path)))
    _failing_router(client.app.state.platform, monkeypatch, exc)
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    [err] = [d for ev, d in _parse_sse(r.text) if ev == "error"]
    assert err["detail"] == str(exc), "detail is unchanged"
    assert err["title"] == "Claude Code isn't signed in"
    assert err["fix"] == f"Press Sign in beside Claude Code in {ACCOUNTS_PLACE}, then retry."
    for raw in ("claude-cli", "/login", "terminal"):
        assert raw not in err["title"] + err["fix"], raw


def test_a_headless_caller_still_catches_an_http_exception_with_the_same_detail(tmp_path, monkeypatch):
    """The phone poller catches HTTPException and reads .detail: unchanged."""
    import asyncio

    from fastapi import HTTPException

    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.daemon.schemas import ChatBody

    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    _failing_router(p, monkeypatch, ProviderError(OPENROUTER_401, status_code=401))
    body = ChatBody(messages=[{"role": "user", "content": "hi"}])
    with pytest.raises(HTTPException) as ei:
        asyncio.run(run_chat_turn(p, {}, body))
    assert ei.value.status_code == 502 and ei.value.detail == OPENROUTER_401


# --------------------------------------------------------------------------- #
# A failed run's summary: plain words, the class to the log.
# --------------------------------------------------------------------------- #
def _summary(engine, sid) -> str:
    with session_scope(engine) as db:
        return db.get(Session, sid).summary or ""


async def test_a_failed_run_summary_is_the_plain_headline_not_the_exception_class(tmp_path, caplog):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("go", AgentType.BUILDER)
    exc = ProviderError(OPENROUTER_401, status_code=401)
    with caplog.at_level("WARNING"):
        await orch._finalize_failed(s, exc)
    summary = _summary(p.engine, s.id)
    assert summary == f"The run failed: OpenRouter refused your key. Update it in {ACCOUNTS_PLACE}."
    assert "ProviderError" not in summary and "No auth credentials" not in summary
    assert any("ProviderError" in r.getMessage() for r in caplog.records), "the class goes to the log"


async def test_an_unrecognised_failure_keeps_its_own_words_without_the_class(tmp_path):
    """Readiness 12A (G28), moved on purpose: an unrecognised failure no
    longer reads its RAW text on the summary (the run page, the Schedules
    row, the bell and the phone all print it). It says what failed and what
    to do next; the failure's own words are kept apart, for the run page's
    folded Details (``Session.failure_detail``)."""
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    s = await orch.create_session("go", AgentType.BUILDER)
    await orch._finalize_failed(s, RuntimeError("the build script exited 2"))
    assert _summary(p.engine, s.id) == (
        "The run failed: Iron Jarvis couldn't finish this task. Run it again. If it happens "
        "again, the run's Details in Iron Jarvis say what went wrong."
    )
    with session_scope(p.engine) as db:
        assert db.get(Session, s.id).failure_detail == "the build script exited 2"
