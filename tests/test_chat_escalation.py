"""One surface: chat escalates itself to the agent (v1.108.0).

REPORTED: "I ran the redaction tool, the LLM stated I need to be in agent mode
to do this. When in chat can't chat and agent mode be one in the same? I don't
see why I need to select between the two — that is not in keeping with
simplicity for the user."

Right on both counts. A mode picker asks the user to route the request before
they have typed it, and getting it wrong produced the worst possible outcome:
the app telling them to go flip a switch and ask again.

So the picker is gone and chat decides for itself. ``escalate_to_agent`` is NOT
a registry tool — nothing executes, nothing is permitted. It is a declared EXIT:
the model calls it, the turn stops, and the client re-runs the SAME message as a
full agent session. That makes the decision deterministic and visible in the
transcript, instead of a sentence of prose the user has to act on.

Round exhaustion escalates too. "I stopped after 6 rounds, 3 calls not
executed" is the same hand-off wearing a different hat.
"""

from __future__ import annotations

import pytest

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import ToolCall


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _force_calls(client, monkeypatch, calls_per_round):
    """Make the mock provider emit chosen tool_calls, round by round."""
    platform = client.app.state.platform
    real_get = platform.providers.get
    rounds = {"n": 0}

    def spy(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def complete(*, system, messages, tools):
            resp = await real_complete(system=system, messages=messages, tools=tools)
            i = rounds["n"]
            rounds["n"] += 1
            resp.tool_calls = calls_per_round(i)
            return resp

        adapter.complete = complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy)
    return rounds


def _chat(client, **kw):
    body = {"messages": [{"role": "user", "content": "do the thing"}]}
    body.update(kw)
    return client.post("/chat", json=body)


# --- the exit is always available -------------------------------------------


def test_the_escalation_exit_rides_every_turn(client, monkeypatch):
    """Including turns with NO armed tools — "I need more than this" has to be
    sayable precisely when the turn was given nothing to work with."""
    seen = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            seen["tools"] = [t.get("name") for t in tools]
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    assert _chat(client).status_code == 200
    # v1.120.0: BOTH declared exits ride every turn — escalation and the
    # workflow-draft proposal (tests/test_workflow_chat_synergy.py owns the
    # draft side's behavior).
    assert seen["tools"] == ["escalate_to_agent", "workflow_draft"]


def test_a_normal_turn_does_not_escalate(client):
    assert _chat(client).json()["escalate"] is False


# --- the model asks for it ---------------------------------------------------


def test_calling_the_exit_escalates_the_turn(client, monkeypatch):
    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id="1", name="escalate_to_agent",
                            arguments={"reason": "needs to edit several files"})]
        if i == 0
        else [],
    )
    body = _chat(client).json()
    assert body["escalate"] is True
    assert body["escalate_reason"] == "needs to edit several files"


def test_it_escalates_even_with_no_tools_armed(client, monkeypatch):
    """The guard right below this check is `if not calls or not armed: break`,
    which would drop the escalation on the floor for a turn with no armed tools
    — i.e. exactly the plain-chat turn most likely to need it."""
    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id="1", name="escalate_to_agent", arguments={"reason": "x"})]
        if i == 0
        else [],
    )
    body = _chat(client).json()  # note: no "tools" armed
    assert body["escalate"] is True


def test_the_exit_never_executes_anything(client, monkeypatch):
    """It is a signal, not a tool. If it ever reached the registry it would be
    an unknown-tool error in the transcript."""
    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id="1", name="escalate_to_agent", arguments={"reason": "x"})]
        if i == 0
        else [],
    )
    body = _chat(client).json()
    assert body["tools_used"] == []


def test_a_missing_reason_still_escalates(client, monkeypatch):
    """A model that forgets the required field must not silently strand the
    user in a turn that goes nowhere."""
    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id="1", name="escalate_to_agent", arguments={})]
        if i == 0
        else [],
    )
    assert _chat(client).json()["escalate"] is True


# --- running out of road escalates too ---------------------------------------


def test_exhausting_the_round_budget_escalates(client, monkeypatch):
    """Chat stopping with work still queued is the same hand-off in disguise."""
    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id=str(i), name="list_folder", arguments={"path": "."})],
    )
    body = _chat(client, tools=["list_folder"]).json()
    assert body["escalate"] is True
    assert body["escalate_reason"]


def test_the_streaming_path_agrees(client, monkeypatch):
    """The client uses /chat/stream; /chat is only its fallback. An escalation
    that only the fallback reports would never fire in the real app."""
    import json

    _force_calls(
        client,
        monkeypatch,
        lambda i: [ToolCall(id="1", name="escalate_to_agent",
                            arguments={"reason": "needs the full agent"})]
        if i == 0
        else [],
    )
    with client.stream(
        "POST", "/chat/stream",
        json={"messages": [{"role": "user", "content": "do the thing"}]},
    ) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "escalate" in payload:
                    done = payload
    assert done is not None, "no done frame carried the decision"
    assert done["escalate"] is True
    assert done["escalate_reason"] == "needs the full agent"


# --- the description is the only thing stopping runaway escalation -----------


def test_the_exit_is_described_as_a_last_resort():
    """Nothing enforces restraint here — an over-eager model would make every
    answer pay a session spin-up, which is the exact cost the merge avoids. The
    wording is the control, so pin it."""
    from iron_jarvis.daemon.routes.chat import _ESCALATE_SPEC

    desc = _ESCALATE_SPEC["description"].lower()
    assert "only when" in desc
    assert "do not call it" in desc
    # And it must kill the behaviour that started all this.
    assert "never tell the user to switch modes" in desc


def test_the_system_prompt_no_longer_teaches_the_model_about_modes(client, monkeypatch):
    """THE root cause. The chat system prompt used to end with "For multi-step
    jobs with tools, the user can switch this conversation to Agent mode." —
    so when a request outgrew the turn the model dutifully said exactly that,
    which is the app asking the user to do its own routing.

    Checked on the live prompt rather than the source string, because both the
    streaming and non-streaming paths build it and only one of them is what the
    real client uses.
    """
    seen = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def c(*, system, messages, tools):
            seen["system"] = system
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = c
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    _chat(client)
    sys_prompt = seen["system"].lower()
    assert "agent mode" not in sys_prompt
    assert "no modes" in sys_prompt
    assert "escalate_to_agent" in sys_prompt
