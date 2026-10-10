"""v1.330.0 (calm chat W13 O1): the hand-off note never fires on code talk.

The wave-12 audit's disclosed edge: on a text-only turn a builtin agent name
that is ALSO an everyday software word drew a false "_Note: nothing was handed
off..._" under ordinary code talk ("Then I pass it to the planner, which
optimizes the query."). The words that can match this way are planner,
reviewer, supervisor, memory and guide.

Now such a word counts only with agent context: "@planner", "the planner
agent", written as a name ("the Planner", "**planner**"), a person-like
continuation ("who", "will", "can", "to do"), "I've asked the reviewer to",
or a bare countable noun ("to reviewer"). "which" / "that" / "function" /
"class" / "module" after it, or code formatting, mean code. Distinct names
(builder, a roster custom agent) keep the old rule. The real glm wordings
pinned in test_text_only_handoff_v1330.py stay claims.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.types import AgentType
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMAdapter, LLMResponse

_NOTE = "\n\n_Note: nothing was handed off. This model answered text only._"

#: Code talk with each of the five words. None may ever get the note.
_CODE_TALK = {
    "planner": [
        "Then I pass it to the planner, which optimizes the query.",
        "I pass it to the planner that builds the execution plan.",
        "Then I pass it to the Planner class, and it returns a plan.",
        "I pass it to `planner` and print the plan.",
        "Then I pass it to the planner.",
    ],
    "reviewer": [
        "I hand it over to the reviewer function, which flags style issues.",
        "Then I pass it to the Reviewer, which returns a list of findings.",
        "I'll pass it to the reviewer module to lint the diff.",
        "Next I send it to `reviewer` for the lint step.",
    ],
    "supervisor": [
        "Then I pass it to the supervisor process that restarts the worker.",
        "I hand it off to the supervisor, which respawns crashed children.",
        "I'll send it to the Supervisor class to schedule.",
    ],
    "memory": [
        "Then I pass it to memory, which caches it.",
        "Next, I send it to memory and read it back later.",
        "I pass it to the memory module so the lookup is fast.",
        "I'll hand it over to the memory store for the next request.",
    ],
    "guide": [
        "I pass it to the guide module to render the steps.",
        "Then I pass it to the guide, which formats the tour.",
        "I'll send it to the Guide component that draws the tooltip.",
    ],
}

#: Agent context for each of the five words. Each must keep the note.
_CLAIMS = {
    "planner": [
        "I'm passing this to the Planner, who will break it into steps.",
        "I'm handing this to the planner agent now.",
        "I'll hand it to @planner.",
        "I'm passing this to planner.",
        # Emphasis alone names it (lowercase, with an article, no follower).
        "I'm handing this to the **planner** now.",
    ],
    "reviewer": [
        "I'll hand it to @reviewer.",
        "I'm handing it to the reviewer to do a final check.",
        # Pinned in test_text_only_handoff_v1330.py: bare, so a name.
        "I’m handing it over to reviewer for a second look.",
        "I have asked the reviewer to check it.",
    ],
    "supervisor": [
        "I'm passing this to the supervisor, who can run the team.",
        "I'm handing it over to **supervisor** now.",
    ],
    "memory": [
        "I'm passing your request to **memory** to do the lookup.",
        "I'm passing this to **memory** now.",
        "I'm handing this to the memory agent.",
        "I'm passing this to Memory, who will remember it for you.",
    ],
    "guide": [
        "I've handed it over to the guide, who will walk you through it.",
        "I'm passing this to the Guide now.",
        "I'll hand it to @guide.",
    ],
}

#: The real wordings and the distinct names: unchanged.
_KEPT = [
    "I'm passing this to builder, who can run commands on your machine. "
    "They'll list the project folder contents and report back.",
    "I'm handing this to **builder**, who can run commands on your machine.",
    "I've delegated this to researcher.",
    "I am going to pass your request to automation.",
    "I've escalated your request to the maintainer.",
    "Escalating to **builder**: list the files.",
    "I'll pass it to @builder now.",
]


def _flat(d: dict) -> list[tuple[str, str]]:
    return [(word, s) for word, sentences in d.items() for s in sentences]


@pytest.mark.parametrize("word,reply", _flat(_CODE_TALK))
def test_code_talk_with_a_common_word_builtin_gets_no_note(word, reply):
    assert word in chat_turn._COMMON_WORD_AGENTS
    assert chat_turn._claimed_handoff_note(reply) == "", reply


@pytest.mark.parametrize("word,reply", _flat(_CLAIMS))
def test_a_common_word_builtin_with_agent_context_keeps_the_note(word, reply):
    assert chat_turn._claimed_handoff_note(reply) == _NOTE, reply


@pytest.mark.parametrize("reply", _KEPT)
def test_distinct_names_keep_todays_rule(reply):
    assert chat_turn._claimed_handoff_note(reply) == _NOTE, reply


def test_the_audit_probe_verbatim():
    probe = "Then I pass it to the planner, which optimizes the query."
    assert chat_turn._claimed_handoff_note(probe) == ""
    # The same five words as code talk, with every roster name present.
    names = chat_turn.handoff_receivers(None) + ("custom:tax-helper", "tax-helper")
    for _word, reply in _flat(_CODE_TALK):
        assert chat_turn._claimed_handoff_note(reply, receivers=names) == "", reply


def test_a_custom_agent_keeps_the_plain_rule():
    names = chat_turn.handoff_receivers(None) + ("custom:tax-helper", "tax-helper")
    reply = "Then I pass it to the tax-helper."  # no context needed for a distinct name
    assert chat_turn._claimed_handoff_note(reply, receivers=names) == _NOTE


def test_every_builtin_is_sorted_into_common_or_distinct():
    """A NEW builtin agent type must be put on one side on purpose: a
    common software word needs agent context, a distinct name does not."""
    distinct = {"builder", "researcher", "automation", "maintainer"}
    assert {t.value for t in AgentType} == chat_turn._COMMON_WORD_AGENTS | distinct
    assert not (chat_turn._COMMON_WORD_AGENTS & distinct)


# ------------------------------------------------------------- the real app

_PID = "fleet-sparkl4"


class _Fleet(LLMAdapter):
    """A fleet endpoint whose tool use is not verified (a text-only pick)."""

    provider = _PID
    model = "glm"

    def __init__(self, reply: str) -> None:
        self.node = SimpleNamespace(label="Spark proxy (L4)")
        self._reply = reply

    def capabilities(self):
        return {"provider": _PID, "model": "glm", "tool_use": False, "vision": False}

    async def complete(self, *, system, messages, tools, **kw):
        return LLMResponse(text=self._reply)


@pytest.fixture
def client(tmp_path) -> TestClient:
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _wire(client, reply: str) -> None:
    fake = _Fleet(reply)
    platform = client.app.state.platform
    real_get, real_avail = platform.providers.get, platform.providers.available
    platform.providers.get = lambda p, m=None: fake if p == _PID else real_get(p, m)
    platform.providers.available = lambda n: True if n == _PID else real_avail(n)


def _done(text: str) -> dict:
    frames = []
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if not lines or lines[0] != "event: done":
            continue
        frames.append(json.loads("".join(ln[6:] for ln in lines[1:] if ln.startswith("data: "))))
    assert frames, text[-2000:]
    return frames[-1]


_PICK = {
    "messages": [{"role": "user", "content": "explain how the query is built"}],
    "provider": _PID,
    "model": "glm",
    "auto_tools": True,
}


def _both_lanes(client) -> list[str]:
    flat = client.post("/chat", json=_PICK)
    streamed = client.post("/chat/stream", json=_PICK)
    assert flat.status_code == 200, flat.text
    assert streamed.status_code == 200, streamed.text
    return [flat.json()["reply"], _done(streamed.text)["reply"]]


def test_code_talk_with_all_five_words_gets_no_note_on_both_lanes(client):
    reply = " ".join(sentences[0] for sentences in _CODE_TALK.values())
    _wire(client, reply)
    for got in _both_lanes(client):
        assert got == reply  # byte for byte: no note appended


def test_a_named_common_word_claim_gets_the_note_on_both_lanes(client):
    reply = "I'm passing this to the Planner, who will break it into steps."
    _wire(client, reply)
    for got in _both_lanes(client):
        assert got == reply + _NOTE
