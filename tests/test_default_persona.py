"""Default persona end-to-end (v1.141.0, Pair Z).

``config.default_persona`` (slug or free text, the same contract as
ChatBody.persona) is consulted whenever a turn carries no explicit persona.
Covered here:

- the setting rides the generic ``_SETTINGS_KEYS`` path: GET exposes it, PUT
  validates + persists it to config.toml, and a fresh app on the same root
  re-reads it (restart survival);
- the integrated chat path: a configured default's PROMPT reaches the model's
  system prompt on a persona-less POST /chat (and the /chat/stream mirror),
  an explicit body persona still wins, and a user's override of the default
  slug wins over the raw built-in.

The integration tests depend on Pair X's call-site one-liners in
daemon/chat_turn.py + daemon/routes/chat.py (``resolve_prompt(...,
default=platform.config.default_persona)``); they skip cleanly (with a loud
reason) while those have not landed — the probe reads the live module source.
"""

from __future__ import annotations

import inspect
import re

import pytest
import tomllib
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _spy(client, captured, reply="hey there!"):
    """Capture the system prompt the /chat turn hands the model (offline)."""
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)

        async def canned(*, system, messages, tools):
            from iron_jarvis.providers.adapters.base import LLMResponse

            captured["system"] = system
            captured["messages"] = messages
            return LLMResponse(text=reply, tool_calls=[], usage={})

        adapter.complete = canned
        return adapter

    platform.providers.get = spy_get


def _default_landed(module) -> bool:
    """True when Pair X's default-persona call-site wiring is present in
    *module*'s live source — either a direct ``resolve_prompt(..., default=)``
    or (the shape X shipped) a ``_resolve_persona(...)`` call fed
    ``config.default_persona``."""
    src = inspect.getsource(module)
    return bool(
        re.search(r"resolve_prompt\(.{0,300}?\bdefault\s*=", src, re.DOTALL)
        or re.search(r"_resolve_persona\(.{0,300}?default_persona", src, re.DOTALL)
    )


def _x_chat_turn_landed() -> bool:
    from iron_jarvis.daemon import chat_turn

    return _default_landed(chat_turn)


def _x_stream_landed() -> bool:
    from iron_jarvis.daemon.routes import chat as routes_chat

    return _default_landed(routes_chat)


# --------------------------------------------------------------------------- #
# (1) The setting exists: GET exposes the "assistant" default.
# --------------------------------------------------------------------------- #
def test_settings_exposes_default_persona(tmp_path):
    client = _client(tmp_path)
    r = client.get("/settings")
    assert r.status_code == 200
    assert r.json()["settings"]["default_persona"] == "assistant"


# --------------------------------------------------------------------------- #
# (2) PUT persists to config.toml and survives a fresh app on the same root.
# --------------------------------------------------------------------------- #
def test_put_default_persona_persists_and_survives_restart(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as client:
        r = client.put("/settings", json={"values": {"default_persona": "accountant"}})
        assert r.status_code == 200
        assert "default_persona" in r.json()["updated"]
        assert r.json()["settings"]["default_persona"] == "accountant"
        # Live config mutated too (what the very next turn reads).
        assert client.app.state.platform.config.default_persona == "accountant"

        # Persisted to <home>/config.toml (the atomic-write path).
        cfg_path = client.app.state.platform.config.home / "config.toml"
        assert cfg_path.exists()
        with cfg_path.open("rb") as fh:
            assert tomllib.load(fh)["default_persona"] == "accountant"

    # A second app on the SAME root re-reads the persisted value.
    with TestClient(create_app(root)) as client2:
        assert client2.get("/settings").json()["settings"]["default_persona"] == "accountant"
        assert client2.app.state.platform.config.default_persona == "accountant"


# --------------------------------------------------------------------------- #
# (3) A wrong-typed value is rejected (400) and nothing is persisted.
# --------------------------------------------------------------------------- #
def test_put_default_persona_wrong_type_rejected(tmp_path):
    client = _client(tmp_path)
    r = client.put("/settings", json={"values": {"default_persona": ["not", "a", "string"]}})
    assert r.status_code == 400
    assert client.get("/settings").json()["settings"]["default_persona"] == "assistant"


# --------------------------------------------------------------------------- #
# (4) INTEGRATED: a persona-less POST /chat runs under the configured default
#     — its PROMPT text appears in the captured system prompt. Depends on
#     Pair X's chat_turn.py one-liner.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not _x_chat_turn_landed(),
    reason="Pair X's resolve_prompt(default=...) one-liner not in chat_turn.py yet",
)
def test_chat_uses_configured_default_persona(tmp_path):
    client = _client(tmp_path)
    # A CUSTOM persona as the default (slug minted from the title).
    r = client.post(
        "/chat/personas",
        json={"title": "Tax Ninja", "prompt": "TAX-NINJA-PROMPT: you are a stealthy CPA."},
    )
    assert r.json()["created"] == "tax-ninja"
    assert client.put(
        "/settings", json={"values": {"default_persona": "tax-ninja"}}
    ).status_code == 200

    captured: dict = {}
    _spy(client, captured)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "TAX-NINJA-PROMPT" in captured["system"]

    # An EXPLICIT body persona still beats the configured default.
    client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "persona": "accountant"},
    )
    assert "TAX-NINJA-PROMPT" not in captured["system"]
    assert "CPA" in captured["system"]

    # A FREE-TEXT default applies verbatim (same contract as ChatBody.persona).
    client.put(
        "/settings",
        json={"values": {"default_persona": "You are a pirate. Answer in pirate speak."}},
    )
    client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert "pirate" in captured["system"]


# --------------------------------------------------------------------------- #
# (5) INTEGRATED: a user's OVERRIDE of the default slug wins over the raw
#     built-in (the empty-want raw-builtin quirk, fixed).
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not _x_chat_turn_landed(),
    reason="Pair X's resolve_prompt(default=...) one-liner not in chat_turn.py yet",
)
def test_chat_default_persona_override_wins(tmp_path):
    client = _client(tmp_path)
    assert client.put(
        "/settings", json={"values": {"default_persona": "developer"}}
    ).status_code == 200
    assert client.put(
        "/chat/personas/developer",
        json={"title": "My Dev", "description": "d", "prompt": "MY-DEV-OVERRIDE prompt."},
    ).status_code == 200

    captured: dict = {}
    _spy(client, captured)
    assert client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    ).status_code == 200
    assert "MY-DEV-OVERRIDE" in captured["system"]


# --------------------------------------------------------------------------- #
# (6) INTEGRATED (stream mirror): the persona-less /chat/stream turn runs
#     under the configured default too. Depends on Pair X's routes/chat.py
#     one-liner (the lock-step inline copy).
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not _x_stream_landed(),
    reason="Pair X's resolve_prompt(default=...) one-liner not in routes/chat.py yet",
)
def test_chat_stream_uses_configured_default_persona(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform

    client.post(
        "/chat/personas",
        json={"title": "Tax Ninja", "prompt": "TAX-NINJA-PROMPT: you are a stealthy CPA."},
    )
    assert client.put(
        "/settings", json={"values": {"default_persona": "tax-ninja"}}
    ).status_code == 200

    captured: dict = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None):
        captured["system"] = system
        adapter = platform.providers.get(
            provider or platform.router.default_provider, model
        )
        async for frame in adapter.stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                yield {**frame, "provider": adapter.provider, "model": adapter.model}
            else:
                yield frame

    platform.router.stream = fake_stream

    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert "TAX-NINJA-PROMPT" in captured["system"]


# --------------------------------------------------------------------------- #
# (7) INTEGRATED (phone reach): a comm chat turn that carries NO persona (the
#     phone never sends one — ChatBody.persona defaults to "") runs under the
#     configured default too. The poller is wired with the REAL turn service
#     and the app's OWN builtin dict via ``app.state.inbound_poller.personas``
#     — which also pins app.py's ``inbound_poller.personas = _PERSONAS``
#     completion: with an empty dict the default slug would degrade into
#     literal free-text instructions ("assistant") instead of the prompt.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(
    not _x_chat_turn_landed(),
    reason="Pair X's resolve_prompt(default=...) one-liner not in chat_turn.py yet",
)
async def test_phone_turn_runs_under_configured_default_persona(tmp_path):
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.comm import InboundMessage, MockChannel, Notifier
    from iron_jarvis.comm.inbound import InboundPoller
    from iron_jarvis.comm.threads import CommThreadStore
    from iron_jarvis.daemon.chat_turn import run_chat_turn

    app = create_app(str(tmp_path))
    client = TestClient(app)
    client.post(
        "/chat/personas",
        json={"title": "Tax Ninja", "prompt": "TAX-NINJA-PROMPT: you are a stealthy CPA."},
    )
    assert client.put(
        "/settings", json={"values": {"default_persona": "tax-ninja"}}
    ).status_code == 200

    # The production wiring must hand the poller the real builtin dict.
    builtins = app.state.inbound_poller.personas
    assert "assistant" in builtins

    captured: dict = {}
    _spy(client, captured, reply="On it, boss.")

    class _PhoneChannel(MockChannel):
        supports_inbound = True

        def has_credentials(self) -> bool:
            return True

    ch = _PhoneChannel(
        {"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]}
    )
    assert ch.chat_enabled()
    platform = app.state.platform
    notifier = Notifier()
    notifier.add_channel("tg", ch)
    poller = InboundPoller(
        notifier,
        Orchestrator(platform),
        platform.engine,
        event_bus=platform.event_bus,
        thread_store=CommThreadStore(platform.engine),
        chat_turn=run_chat_turn,
        personas=builtins,
        platform=platform,
    )

    res = await poller._handle(
        "tg",
        ch,
        InboundMessage(sender_id="777", text="hi from my phone", update_id=1, reply_to="777"),
    )

    assert res["status"] == "chat"  # the full-chat lane, not the legacy one-shot
    # The DEFAULT persona's PROMPT reached the model — not the raw builtin,
    # not the literal slug as free text.
    assert "TAX-NINJA-PROMPT" in captured["system"]
    assert captured["system"].count("tax-ninja") == 0  # slug never used as text
    assert any("On it, boss." in m for m in ch.sent)  # reply delivered
